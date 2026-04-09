"""质量画像模块最小实现服务。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import QualityAsyncSessionLocal
from app.core.logging_config import get_logger
from app.models.quality import (
    AiAnalysisReport,
    AnalysisTask,
    Course,
    Lesson,
    LessonAsrPayload,
    OcrSegment,
    QualityTaxonomyTerm,
)
from app.schemas.quality import QualityDataIngestionRequest

logger = get_logger(__name__)


class QualityServiceError(Exception):
    """质量画像业务异常。"""

    def __init__(self, http_status: int, code: int, message: str, data: Optional[Dict[str, Any]] = None):
        self.http_status = http_status
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


TASK_STATUS_NAME = {
    0: "queued",
    1: "running",
    2: "success",
    3: "failed",
    4: "cancelled",
}


LESSON_STATUS_NAME = {
    0: "pending",
    1: "ready",
    2: "analyzing",
    3: "success",
    4: "failed",
}


VALID_REPORT_LEVELS = {"lesson", "week", "semester"}
VALID_MODULES = {
    "radar",
    "ideology_map",
    "bloom_evolution",
    "challenge_pace_trend",
    "innovation_profile",
    "atmosphere_cross_diagnosis",
    # lesson 级模块（最小实现）
    "bloom",
    "pace_challenge",
    "ideology_innovation",
    "atmosphere",
}


def now_utc() -> datetime:
    """UTC 时间。"""
    return datetime.utcnow()


def build_dedupe_key(course_id: str, task_kind: str, target_week: int) -> str:
    """构建任务去重键。"""
    return f"{course_id}:{task_kind}:{target_week}"


def status_name(code: int) -> str:
    """状态码转字符串。"""
    return TASK_STATUS_NAME.get(code, "unknown")


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _avg(values: List[float]) -> float:
    vals = [v for v in values if v is not None]
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def _extract_asr_stats(asr_json: Any) -> Dict[str, float]:
    """提取 ASR 基础统计。"""
    segments: List[Dict[str, Any]] = asr_json if isinstance(asr_json, list) else []
    if not segments:
        return {
            "avg_speed": 0.0,
            "white_space_rate": 0.0,
            "active_emotions_count": 0.0,
            "total_chars": 0.0,
        }

    speeds = [_safe_float(seg.get("speed"), 0.0) for seg in segments if seg.get("speed") is not None]
    avg_speed = _avg(speeds) if speeds else 0.0

    total_chars = float(sum(len(str(seg.get("text", ""))) for seg in segments))

    # 非“平淡/中性”视作活跃情绪
    neutral = {"平淡", "中性", "neutral", ""}
    active_emotions_count = float(
        sum(1 for seg in segments if str(seg.get("emotion", "")).strip() not in neutral)
    )

    min_bg = min((_safe_float(seg.get("bg"), 0.0) for seg in segments), default=0.0)
    max_ed = max((_safe_float(seg.get("ed"), 0.0) for seg in segments), default=0.0)
    speaking = sum(
        max(0.0, _safe_float(seg.get("ed"), 0.0) - _safe_float(seg.get("bg"), 0.0))
        for seg in segments
    )
    total = max(0.0, max_ed - min_bg)
    if total <= 0:
        white_space_rate = 0.0
    else:
        white_space_rate = max(0.0, min(1.0, 1.0 - speaking / total))

    return {
        "avg_speed": round(avg_speed, 2),
        "white_space_rate": round(white_space_rate, 4),
        "active_emotions_count": active_emotions_count,
        "total_chars": total_chars,
    }


async def _upsert_report(
    db: AsyncSession,
    *,
    course_id: str,
    report_level: str,
    target_id: str,
    module_name: str,
    payload: Dict[str, Any],
    source_task_id: Optional[str] = None,
) -> None:
    existing = await db.scalar(
        select(AiAnalysisReport).where(
            AiAnalysisReport.course_id == course_id,
            AiAnalysisReport.report_level == report_level,
            AiAnalysisReport.target_id == target_id,
            AiAnalysisReport.module_name == module_name,
        )
    )
    if existing:
        existing.report_data = payload
        existing.source_task_id = source_task_id
        existing.updated_at = now_utc()
    else:
        db.add(
            AiAnalysisReport(
                id=str(uuid.uuid4()),
                course_id=course_id,
                report_level=report_level,
                target_id=target_id,
                module_name=module_name,
                report_data=payload,
                source_task_id=source_task_id,
                created_at=now_utc(),
                updated_at=now_utc(),
            )
        )


async def ensure_course(db: AsyncSession, request: QualityDataIngestionRequest) -> Tuple[Course, bool]:
    """确保课程存在。"""
    course = await db.scalar(select(Course).where(Course.id == request.course_id))
    created = False
    if course is None:
        created = True
        course = Course(
            id=request.course_id,
            course_name=request.course_name,
            academic_year=request.academic_year,
            teacher=request.teacher,
            total_weeks=request.total_weeks or 16,
            total_lessons=request.total_lessons or 32,
            created_at=now_utc(),
            updated_at=now_utc(),
        )
        db.add(course)
    else:
        course.course_name = request.course_name
        course.academic_year = request.academic_year
        course.teacher = request.teacher
        if request.total_weeks:
            course.total_weeks = request.total_weeks
        if request.total_lessons:
            course.total_lessons = request.total_lessons
        course.updated_at = now_utc()

    await db.flush()
    return course, created


async def _ensure_taxonomy_seed(db: AsyncSession, course_id: str, course_name: str, week_number: int) -> str:
    """若课程词库不存在则写入最小种子。"""
    exists = await db.scalar(
        select(func.count(QualityTaxonomyTerm.id)).where(QualityTaxonomyTerm.course_id == course_id)
    )
    if exists and int(exists) > 0:
        return "ready"

    seed_terms = [
        QualityTaxonomyTerm(
            id=str(uuid.uuid4()),
            course_id=course_id,
            term_type="ideology",
            category_name="思政引导",
            keyword=f"{course_name}育人",
            confidence=0.5,
            evidence_lessons=1,
            evidence_weeks=1,
            first_seen_week=week_number,
            last_seen_week=week_number,
            created_at=now_utc(),
            updated_at=now_utc(),
        ),
        QualityTaxonomyTerm(
            id=str(uuid.uuid4()),
            course_id=course_id,
            term_type="innovation",
            category_name="前沿主题",
            keyword=f"{course_name}创新",
            confidence=0.5,
            evidence_lessons=1,
            evidence_weeks=1,
            first_seen_week=week_number,
            last_seen_week=week_number,
            created_at=now_utc(),
            updated_at=now_utc(),
        ),
    ]
    db.add_all(seed_terms)
    await db.flush()
    return "triggered"


async def ingest_data(db: AsyncSession, request: QualityDataIngestionRequest) -> Dict[str, Any]:
    """接收并落库多模态数据。"""
    if not request.asr_data:
        raise QualityServiceError(400, 40001, "asr_data 不能为空")
    if not request.ocr_data:
        raise QualityServiceError(400, 40001, "ocr_data 不能为空")

    course, course_created = await ensure_course(db, request)

    # 冲突校验：周内序号占用
    week_index_conflict = await db.scalar(
        select(Lesson).where(
            Lesson.course_id == request.course_id,
            Lesson.week_number == request.week_number,
            Lesson.lesson_index_in_week == request.lesson_index_in_week,
            Lesson.lesson_id != request.lesson_id,
        )
    )
    if week_index_conflict is not None:
        raise QualityServiceError(
            409,
            40902,
            f"周内序号冲突: course_id={request.course_id}, week={request.week_number}, lesson_index_in_week={request.lesson_index_in_week}",
        )

    # 冲突校验：全局序号占用
    global_index_conflict = await db.scalar(
        select(Lesson).where(
            Lesson.course_id == request.course_id,
            Lesson.lesson_index_global == request.lesson_index_global,
            Lesson.lesson_id != request.lesson_id,
        )
    )
    if global_index_conflict is not None:
        raise QualityServiceError(
            409,
            40902,
            f"全局序号冲突: course_id={request.course_id}, lesson_index_global={request.lesson_index_global}",
        )

    lesson = await db.scalar(
        select(Lesson).where(
            Lesson.course_id == request.course_id,
            Lesson.lesson_id == request.lesson_id,
        )
    )

    if lesson is not None and lesson.status == 3:
        raise QualityServiceError(
            409,
            40901,
            f"lesson 已处理完成，不允许覆盖: course_id={request.course_id}, lesson_id={request.lesson_id}",
        )
    if lesson is not None and lesson.status in (1, 2):
        raise QualityServiceError(
            409,
            40902,
            f"lesson 正在处理中，不允许重复提交: course_id={request.course_id}, lesson_id={request.lesson_id}",
        )

    if lesson is None:
        lesson_action = "created"
        lesson = Lesson(
            id=str(uuid.uuid4()),
            course_id=request.course_id,
            lesson_id=request.lesson_id,
            week_number=request.week_number,
            lesson_index_in_week=request.lesson_index_in_week,
            lesson_index_global=request.lesson_index_global,
            avg_head_up_rate=request.avg_head_up_rate,
            score_high_order=None,
            score_innovation=None,
            score_fun_experience=None,
            score_challenge=None,
            score_ideology=None,
            status=1,  # ready
            failed_reason=None,
            created_at=now_utc(),
            updated_at=now_utc(),
        )
        db.add(lesson)
        await db.flush()
    else:
        lesson_action = "updated"
        lesson.week_number = request.week_number
        lesson.lesson_index_in_week = request.lesson_index_in_week
        lesson.lesson_index_global = request.lesson_index_global
        lesson.avg_head_up_rate = request.avg_head_up_rate
        lesson.score_high_order = None
        lesson.score_innovation = None
        lesson.score_fun_experience = None
        lesson.score_challenge = None
        lesson.score_ideology = None
        lesson.status = 1  # ready
        lesson.failed_reason = None
        lesson.updated_at = now_utc()
        await db.flush()

    # ASR upsert
    asr_payload = await db.scalar(select(LessonAsrPayload).where(LessonAsrPayload.lesson_ref_id == lesson.id))
    if asr_payload is None:
        db.add(
            LessonAsrPayload(
                lesson_ref_id=lesson.id,
                asr_json=[seg.model_dump() for seg in request.asr_data],
                created_at=now_utc(),
                updated_at=now_utc(),
            )
        )
    else:
        asr_payload.asr_json = [seg.model_dump() for seg in request.asr_data]
        asr_payload.updated_at = now_utc()

    # OCR replace
    await db.execute(delete(OcrSegment).where(OcrSegment.lesson_ref_id == lesson.id))
    db.add_all(
        [
            OcrSegment(
                lesson_ref_id=lesson.id,
                time_offset=seg.time_offset,
                page_num=seg.page_num,
                ocr_content=seg.ocr_content,
                ocr_keywords=[str(x) for x in (seg.ocr_keywords or []) if str(x).strip()],
                created_at=now_utc(),
            )
            for seg in request.ocr_data
        ]
    )

    taxonomy_action = await _ensure_taxonomy_seed(db, request.course_id, request.course_name, request.week_number)
    await db.commit()

    return {
        "course_id": course.id,
        "lesson_id": lesson.lesson_id,
        "week_number": lesson.week_number,
        "lesson_index_in_week": lesson.lesson_index_in_week,
        "lesson_index_global": lesson.lesson_index_global,
        "lesson_status": lesson.status,
        "lesson_status_name": LESSON_STATUS_NAME.get(lesson.status, "unknown"),
        "course_created": course_created,
        "lesson_action": lesson_action,
        "taxonomy_action": taxonomy_action,
    }


async def resolve_target_week(db: AsyncSession, course_id: str, requested_target_week: Optional[int]) -> Tuple[int, str]:
    """解析 target_week。"""
    if requested_target_week is not None:
        return requested_target_week, "request"

    max_week = await db.scalar(
        select(func.max(Lesson.week_number)).where(Lesson.course_id == course_id)
    )
    if max_week is None:
        raise QualityServiceError(
            409,
            20410,
            f"课程暂无可用课时数据，无法解析 target_week: course_id={course_id}",
        )
    return int(max_week), "resolved_latest"


async def create_or_mark_semester_task(
    db: AsyncSession,
    *,
    course_id: str,
    target_week: int,
    force_run: bool,
    target_week_source: str,
) -> Tuple[AnalysisTask, bool]:
    """创建任务或命中去重后打标记。返回(task, dedupe_hit)。"""
    dedupe_key = build_dedupe_key(course_id, "semester_profile", target_week)
    existing = await db.scalar(
        select(AnalysisTask)
        .where(
            AnalysisTask.dedupe_key == dedupe_key,
            AnalysisTask.status.in_([0, 1]),
        )
        .order_by(AnalysisTask.created_at.desc())
    )
    if existing is not None:
        if force_run and not bool(existing.force_run):
            existing.force_run = True
        if not existing.requeue_needed:
            existing.requeue_needed = True
        existing.updated_at = now_utc()
        await db.commit()
        return existing, True

    task = AnalysisTask(
        id=str(uuid.uuid4()),
        course_id=course_id,
        task_kind="semester_profile",
        target_week=target_week,
        status=0,
        force_run=force_run,
        dedupe_key=dedupe_key,
        requeue_needed=False,
        cancel_requested=False,
        current_node="queued",
        graph_state={"progress_pct": 0, "target_week_source": target_week_source},
        attempts=0,
        max_attempts=3,
        created_at=now_utc(),
        updated_at=now_utc(),
    )
    db.add(task)
    await db.commit()
    return task, False


async def _set_task_failed(db: AsyncSession, task: AnalysisTask, reason: str) -> None:
    task.status = 3
    task.current_node = "failed"
    task.failed_reason = reason[:2000]
    task.finished_at = now_utc()
    task.updated_at = now_utc()
    progress = ((task.graph_state or {}).get("progress_pct") if task.graph_state else 0) or 0
    task.graph_state = {**(task.graph_state or {}), "progress_pct": progress}
    await db.commit()


async def _set_task_cancelled(db: AsyncSession, task: AnalysisTask) -> None:
    task.status = 4
    task.current_node = "cancelled"
    task.cancelled_at = now_utc()
    task.finished_at = now_utc()
    task.updated_at = now_utc()
    task.graph_state = {**(task.graph_state or {}), "progress_pct": (task.graph_state or {}).get("progress_pct", 0)}
    await db.commit()


async def _cancel_if_requested(db: AsyncSession, task: AnalysisTask) -> bool:
    """边界检查：若收到取消请求则终止任务。"""
    await db.refresh(task)
    if bool(task.cancel_requested):
        await _set_task_cancelled(db, task)
        return True
    return False


async def run_lesson_analysis_background(course_id: str, lesson_id: str) -> None:
    """最小课时分析后台任务。"""
    async with QualityAsyncSessionLocal() as db:
        lesson = await db.scalar(
            select(Lesson).where(
                Lesson.course_id == course_id,
                Lesson.lesson_id == lesson_id,
            )
        )
        if lesson is None:
            logger.warning(f"[quality] lesson not found: course_id={course_id}, lesson_id={lesson_id}")
            return

        lesson.status = 2
        lesson.updated_at = now_utc()
        await db.commit()

        try:
            asr_payload = await db.scalar(
                select(LessonAsrPayload).where(LessonAsrPayload.lesson_ref_id == lesson.id)
            )
            stats = _extract_asr_stats(asr_payload.asr_json if asr_payload else [])
            avg_head = _safe_float(lesson.avg_head_up_rate, 0.0)

            # lesson 级占位模块
            bloom_high = min(90, max(10, int(20 + stats["active_emotions_count"] * 6 + stats["avg_speed"] * 0.1)))
            bloom_mid = min(80, max(5, int(35 + min(stats["total_chars"] / 1500.0, 30))))
            if bloom_high + bloom_mid > 95:
                bloom_mid = max(5, 95 - bloom_high)
            bloom_low = max(0, 100 - bloom_high - bloom_mid)

            bloom_payload = {
                "high_score": bloom_high,
                "mid_score": bloom_mid,
                "low_score": bloom_low,
            }
            pace_payload = {
                "avg_speed": stats["avg_speed"],
                "white_space_rate": stats["white_space_rate"],
            }

            terms = (
                await db.execute(
                    select(QualityTaxonomyTerm).where(QualityTaxonomyTerm.course_id == course_id)
                )
            ).scalars().all()
            ideology_hits = [
                {"category": t.category_name, "keyword": t.keyword, "count": int(t.evidence_lessons or 1)}
                for t in terms
                if t.term_type == "ideology"
            ][:10]
            innovation_hits = [
                {"category": t.category_name, "keyword": t.keyword, "count": int(t.evidence_lessons or 1)}
                for t in terms
                if t.term_type == "innovation"
            ][:10]

            ideology_payload = {
                "ideology_hits": ideology_hits,
                "innovation_hits": innovation_hits,
            }

            atmosphere_payload = {
                "avg_head_up_rate": round(avg_head, 4),
                "active_emotions_count": int(stats["active_emotions_count"]),
            }

            # 课时五类分值（用于周/学期聚合）
            avg_speed = _safe_float(stats["avg_speed"], 0.0)
            white_space_rate = _safe_float(stats["white_space_rate"], 0.0)
            challenge_score = max(
                0.0,
                min(
                    100.0,
                    0.55 * min(avg_speed / 2.0, 100.0) + 0.45 * (100.0 * (1.0 - white_space_rate)),
                ),
            )
            ideology_score = max(0.0, min(100.0, 50.0 + len(ideology_hits) * 5.0))
            innovation_score = max(0.0, min(100.0, 50.0 + len(innovation_hits) * 5.0))
            fun_score = max(0.0, min(100.0, avg_head * 100.0))

            await _upsert_report(
                db,
                course_id=course_id,
                report_level="lesson",
                target_id=lesson.lesson_id,
                module_name="bloom",
                payload=bloom_payload,
            )
            await _upsert_report(
                db,
                course_id=course_id,
                report_level="lesson",
                target_id=lesson.lesson_id,
                module_name="pace_challenge",
                payload=pace_payload,
            )
            await _upsert_report(
                db,
                course_id=course_id,
                report_level="lesson",
                target_id=lesson.lesson_id,
                module_name="ideology_innovation",
                payload=ideology_payload,
            )
            await _upsert_report(
                db,
                course_id=course_id,
                report_level="lesson",
                target_id=lesson.lesson_id,
                module_name="atmosphere",
                payload=atmosphere_payload,
            )

            lesson.status = 3
            lesson.score_high_order = round(float(bloom_high), 2)
            lesson.score_innovation = round(float(innovation_score), 2)
            lesson.score_fun_experience = round(float(fun_score), 2)
            lesson.score_challenge = round(float(challenge_score), 2)
            lesson.score_ideology = round(float(ideology_score), 2)
            lesson.failed_reason = None
            lesson.analysis_updated_at = now_utc()
            lesson.updated_at = now_utc()
            await db.commit()

            # 自动触发 semester_profile（以该课所在周为目标周）
            task, dedupe_hit = await create_or_mark_semester_task(
                db,
                course_id=course_id,
                target_week=lesson.week_number,
                force_run=False,
                target_week_source="request",
            )
            if not dedupe_hit:
                asyncio.create_task(run_semester_profile_task_background(task.id))

        except Exception as e:
            await db.rollback()
            lesson = await db.scalar(
                select(Lesson).where(
                    Lesson.course_id == course_id,
                    Lesson.lesson_id == lesson_id,
                )
            )
            if lesson is not None:
                lesson.status = 4
                lesson.failed_reason = str(e)[:2000]
                lesson.updated_at = now_utc()
                await db.commit()
            logger.error(f"[quality] lesson analysis failed: course_id={course_id}, lesson_id={lesson_id}, err={e}")


def _module_payloads_for_semester(
    *,
    course_name: str,
    target_week: int,
    success_lessons: List[Lesson],
    weeks_with_data: List[int],
    missing_weeks: List[int],
    avg_head: float,
    terms: List[QualityTaxonomyTerm],
) -> Dict[str, Dict[str, Any]]:
    analyzed_lessons = len(success_lessons)
    lesson_high_order_scores = [float(l.score_high_order) for l in success_lessons if l.score_high_order is not None]
    lesson_challenge_scores = [float(l.score_challenge) for l in success_lessons if l.score_challenge is not None]
    lesson_ideology_scores = [float(l.score_ideology) for l in success_lessons if l.score_ideology is not None]
    lesson_innovation_scores = [float(l.score_innovation) for l in success_lessons if l.score_innovation is not None]
    lesson_fun_scores = [float(l.score_fun_experience) for l in success_lessons if l.score_fun_experience is not None]

    # 优先使用 lesson 五类分值聚合，缺失时回退占位估算。
    high_order = round(_avg(lesson_high_order_scores), 1) if lesson_high_order_scores else float(min(95, 60 + analyzed_lessons))
    challenge = round(_avg(lesson_challenge_scores), 1) if lesson_challenge_scores else float(min(95, 65 + analyzed_lessons // 2))
    ideology = (
        round(_avg(lesson_ideology_scores), 1)
        if lesson_ideology_scores
        else float(min(95, 70 + min(20, len([t for t in terms if t.term_type == "ideology"]) * 2)))
    )
    innovation = (
        round(_avg(lesson_innovation_scores), 1)
        if lesson_innovation_scores
        else float(min(95, 70 + min(20, len([t for t in terms if t.term_type == "innovation"]) * 2)))
    )
    fun_experience = (
        round(_avg(lesson_fun_scores), 1)
        if lesson_fun_scores
        else float(int(max(0, min(100, avg_head * 100))))
    )
    overall_score = round((high_order + challenge + ideology + innovation + fun_experience) / 5.0, 1)

    radar_payload = {
        "progress_meta": {
            "target_week": target_week,
            "weeks_with_data": weeks_with_data,
            "missing_weeks": missing_weeks,
            "analyzed_lessons": analyzed_lessons,
        },
        "scores": {
            "high_order": high_order,
            "innovation": innovation,
            "fun_experience": fun_experience,
            "ideology": ideology,
            "challenge": challenge,
        },
        "overall_score": overall_score,
        "ai_diagnosis": f"{course_name} 当前阶段画像已生成，已覆盖到第{target_week}周。",
    }

    # 周趋势占位数据
    weekly_map: Dict[int, List[Lesson]] = {}
    for lesson in success_lessons:
        weekly_map.setdefault(int(lesson.week_number), []).append(lesson)

    bloom_weekly = []
    challenge_weekly = []
    atmosphere_weekly = []
    for week in range(1, target_week + 1):
        week_lessons = weekly_map.get(week, [])
        if not week_lessons:
            bloom_weekly.append({"week": week, "high": None, "mid": None, "low": None})
            challenge_weekly.append({"week": week, "info_density": None, "white_space_rate": None})
            atmosphere_weekly.append({"week": week, "avg_head_up": None, "active_interactions": None})
            continue

        week_avg_head = _avg([_safe_float(l.avg_head_up_rate, 0.0) for l in week_lessons])
        high = min(90, 25 + len(week_lessons) * 10)
        mid = min(80, 40 + len(week_lessons) * 5)
        low = max(0, 100 - high - mid)
        bloom_weekly.append({"week": week, "high": high, "mid": mid, "low": low})
        challenge_weekly.append(
            {
                "week": week,
                "info_density": min(100, 60 + len(week_lessons) * 8),
                "white_space_rate": round(max(0.02, 0.15 - len(week_lessons) * 0.02), 4),
            }
        )
        atmosphere_weekly.append(
            {
                "week": week,
                "avg_head_up": round(week_avg_head, 4),
                "active_interactions": len(week_lessons),
            }
        )

    bloom_payload = {
        "weekly_trends": bloom_weekly,
        "ai_interpretation": "认知层级随周次推进呈现逐步跃升趋势。",
    }

    ideology_payload = {
        "word_cloud": [
            {
                "keyword": t.keyword,
                "category": t.category_name,
                "count": int(t.evidence_lessons or 1),
            }
            for t in terms
            if t.term_type == "ideology"
        ][:20],
        "ai_diagnosis": "课程思政关键词已完成阶段性聚合。",
    }

    challenge_payload = {
        "weekly_trends": challenge_weekly,
        "ai_correlation_analysis": "挑战度与课堂节奏已形成周级趋势数据。",
    }

    innovation_payload = {
        "innovation_hits": [
            {
                "keyword": t.keyword,
                "category": t.category_name,
                "count": int(t.evidence_lessons or 1),
            }
            for t in terms
            if t.term_type == "innovation"
        ][:20],
        "ai_diagnosis": "前沿性关键词已完成阶段性聚合。",
    }

    atmosphere_payload = {
        "weekly_trends": atmosphere_weekly,
        "ai_cross_diagnosis": "课堂氛围与趣味性跨周趋势已生成。",
    }

    return {
        "radar": radar_payload,
        "bloom_evolution": bloom_payload,
        "ideology_map": ideology_payload,
        "challenge_pace_trend": challenge_payload,
        "innovation_profile": innovation_payload,
        "atmosphere_cross_diagnosis": atmosphere_payload,
    }


async def run_semester_profile_task_background(task_id: str) -> None:
    """最小学期画像后台任务。"""
    async with QualityAsyncSessionLocal() as db:
        task = await db.scalar(select(AnalysisTask).where(AnalysisTask.id == task_id))
        if task is None:
            return
        if task.status != 0:
            return

        task.status = 1
        task.current_node = "validate_input"
        task.attempts = int(task.attempts or 0) + 1
        if task.started_at is None:
            task.started_at = now_utc()
        task.updated_at = now_utc()
        task.graph_state = {**(task.graph_state or {}), "progress_pct": 10}
        await db.commit()

        try:
            if await _cancel_if_requested(db, task):
                return

            course = await db.scalar(select(Course).where(Course.id == task.course_id))
            if course is None:
                await _set_task_failed(db, task, f"course_id not found: {task.course_id}")
                return

            target_week = int(task.target_week or 0)
            task.current_node = "load_week_data"
            task.graph_state = {**(task.graph_state or {}), "progress_pct": 25}
            task.updated_at = now_utc()
            await db.commit()
            if await _cancel_if_requested(db, task):
                return

            success_lessons = (
                await db.execute(
                    select(Lesson).where(
                        and_(
                            Lesson.course_id == task.course_id,
                            Lesson.status == 3,
                            Lesson.week_number <= target_week,
                        )
                    ).order_by(Lesson.week_number.asc(), Lesson.lesson_index_in_week.asc())
                )
            ).scalars().all()

            if not success_lessons:
                await _set_task_failed(
                    db,
                    task,
                    f"Data not ready: no successful lessons for course_id={task.course_id}, target_week={target_week}",
                )
                return

            weeks_with_data = sorted({int(l.week_number) for l in success_lessons})
            missing_weeks = [w for w in range(1, target_week + 1) if w not in weeks_with_data]
            avg_head = _avg([_safe_float(l.avg_head_up_rate, 0.0) for l in success_lessons])

            terms = (
                await db.execute(
                    select(QualityTaxonomyTerm).where(QualityTaxonomyTerm.course_id == task.course_id)
                )
            ).scalars().all()

            task.current_node = "aggregate_metrics"
            task.graph_state = {**(task.graph_state or {}), "progress_pct": 55}
            task.updated_at = now_utc()
            await db.commit()
            if await _cancel_if_requested(db, task):
                return

            payloads = _module_payloads_for_semester(
                course_name=course.course_name,
                target_week=target_week,
                success_lessons=success_lessons,
                weeks_with_data=weeks_with_data,
                missing_weeks=missing_weeks,
                avg_head=avg_head,
                terms=terms,
            )

            task.current_node = "upsert_reports"
            task.graph_state = {**(task.graph_state or {}), "progress_pct": 85}
            task.updated_at = now_utc()
            await db.commit()
            if await _cancel_if_requested(db, task):
                return

            for module_name, payload in payloads.items():
                await _upsert_report(
                    db,
                    course_id=task.course_id,
                    report_level="semester",
                    target_id=task.course_id,
                    module_name=module_name,
                    payload=payload,
                    source_task_id=task.id,
                )

            requeue_needed = bool(task.requeue_needed)
            task.requeue_needed = False
            task.current_node = "finalize"
            task.status = 2
            task.finished_at = now_utc()
            task.updated_at = now_utc()
            task.graph_state = {**(task.graph_state or {}), "progress_pct": 100}
            await db.commit()

            # 结束后补跑
            if requeue_needed:
                next_task, dedupe_hit = await create_or_mark_semester_task(
                    db,
                    course_id=task.course_id,
                    target_week=target_week,
                    force_run=bool(task.force_run),
                    target_week_source=(task.graph_state or {}).get("target_week_source", "request"),
                )
                if not dedupe_hit:
                    asyncio.create_task(run_semester_profile_task_background(next_task.id))

        except Exception as e:
            await db.rollback()
            task = await db.scalar(select(AnalysisTask).where(AnalysisTask.id == task_id))
            if task is not None:
                await _set_task_failed(db, task, f"semester task failed: {e}")
            logger.error(f"[quality] semester task failed: task_id={task_id}, err={e}")
