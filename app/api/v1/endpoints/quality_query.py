"""质量画像：报表查询端点。"""

import uuid
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.quality import AiAnalysisReport, AnalysisTask, Course, Lesson
from app.schemas.quality import QualityBaseResponse, SemesterProfileModuleQueryRequest
from app.services.quality_service import VALID_MODULES, VALID_REPORT_LEVELS

router = APIRouter()


@router.post("/courses/semester-profile/module/query", response_model=QualityBaseResponse)
async def query_semester_profile_module(
    request: SemesterProfileModuleQueryRequest,
    db: AsyncSession = Depends(get_db),
):
    """查询看板模块数据。"""
    trace_id = uuid.uuid4().hex
    try:
        if request.report_level not in VALID_REPORT_LEVELS:
            return JSONResponse(
                status_code=400,
                content=_resp_body(
                    40001,
                    "Invalid report_level, expected lesson|week|semester",
                    None,
                    trace_id,
                ),
            )
        if request.module_name not in VALID_MODULES:
            return JSONResponse(
                status_code=400,
                content=_resp_body(
                    40001,
                    (
                        "Invalid module_name, expected one of: "
                        + ",".join(sorted(VALID_MODULES))
                    ),
                    None,
                    trace_id,
                ),
            )
        if request.report_level == "semester" and request.target_identifier != request.course_id:
            return JSONResponse(
                status_code=400,
                content=_resp_body(
                    40001,
                    (
                        "Invalid target_identifier for semester level: "
                        f"target_identifier={request.target_identifier}, expected course_id={request.course_id}"
                    ),
                    None,
                    trace_id,
                ),
            )

        course = await db.scalar(select(Course).where(Course.id == request.course_id))
        if course is None:
            return JSONResponse(
                status_code=404,
                content=_resp_body(40401, f"course_id not found: {request.course_id}", None, trace_id),
            )

        report = await db.scalar(
            select(AiAnalysisReport).where(
                AiAnalysisReport.course_id == request.course_id,
                AiAnalysisReport.report_level == request.report_level,
                AiAnalysisReport.target_id == request.target_identifier,
                AiAnalysisReport.module_name == request.module_name,
            )
        )

        if report is not None:
            return QualityBaseResponse(
                code=20000,
                message=(
                    "Dashboard data retrieved for "
                    f"course_id={request.course_id}, level={request.report_level}, "
                    f"target={request.target_identifier}, module={request.module_name}"
                ),
                data={
                    "course_id": request.course_id,
                    "report_level": request.report_level,
                    "target_identifier": request.target_identifier,
                    "module_name": request.module_name,
                    "report_payload": report.report_data,
                    "updated_at": _fmt_dt(report.updated_at),
                    "source_task_id": report.source_task_id,
                },
                trace_id=trace_id,
            )

        waiting_resp = await _build_not_ready_response(db, request, trace_id)
        if waiting_resp is not None:
            return waiting_resp

        return QualityBaseResponse(
            code=20404,
            message=(
                "No report data for "
                f"module={request.module_name}, level={request.report_level}, target={request.target_identifier}"
            ),
            data={
                "course_id": request.course_id,
                "report_level": request.report_level,
                "target_identifier": request.target_identifier,
                "module_name": request.module_name,
                "report_payload": None,
            },
            trace_id=trace_id,
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=_resp_body(
                50001,
                (
                    "Internal error on module query: "
                    f"course_id={request.course_id}, level={request.report_level}, "
                    f"target={request.target_identifier}, module={request.module_name}, err={e}"
                ),
                None,
                trace_id,
            ),
        )


async def _build_not_ready_response(
    db: AsyncSession,
    request: SemesterProfileModuleQueryRequest,
    trace_id: str,
) -> QualityBaseResponse | JSONResponse | None:
    if request.report_level == "lesson":
        lesson = await db.scalar(
            select(Lesson).where(
                Lesson.course_id == request.course_id,
                Lesson.lesson_id == request.target_identifier,
            )
        )
        if lesson is None:
            return JSONResponse(
                status_code=404,
                content=_resp_body(
                    40402,
                    (
                        "lesson not found: "
                        f"course_id={request.course_id}, lesson_id={request.target_identifier}"
                    ),
                    None,
                    trace_id,
                ),
            )
        if lesson.status != 3:
            return QualityBaseResponse(
                code=20410,
                message=(
                    "Data not ready: target lesson not analyzed yet, "
                    f"course_id={request.course_id}, lesson_id={request.target_identifier}, status={lesson.status}"
                ),
                data={
                    "course_id": request.course_id,
                    "report_level": request.report_level,
                    "target_identifier": request.target_identifier,
                    "module_name": request.module_name,
                    "report_payload": None,
                    "missing_summary": {
                        "lesson_id": request.target_identifier,
                        "lesson_status": lesson.status,
                    },
                },
                trace_id=trace_id,
            )
        return None

    if request.report_level == "week":
        try:
            target_week = int(request.target_identifier)
        except (TypeError, ValueError):
            return JSONResponse(
                status_code=400,
                content=_resp_body(
                    40001,
                    (
                        "Invalid target_identifier for week level, "
                        f"expected numeric string, got={request.target_identifier}"
                    ),
                    None,
                    trace_id,
                ),
            )

        lessons = (
            await db.execute(
                select(Lesson).where(
                    Lesson.course_id == request.course_id,
                    Lesson.week_number == target_week,
                )
            )
        ).scalars().all()

        if not lessons:
            return None

        waiting_lesson_ids = [l.lesson_id for l in lessons if l.status != 3]
        if waiting_lesson_ids:
            return QualityBaseResponse(
                code=20410,
                message=(
                    "Data not ready: lessons in target week are not fully analyzed, "
                    f"course_id={request.course_id}, week={target_week}"
                ),
                data={
                    "course_id": request.course_id,
                    "report_level": request.report_level,
                    "target_identifier": request.target_identifier,
                    "module_name": request.module_name,
                    "report_payload": None,
                    "missing_summary": {
                        "week_number": target_week,
                        "waiting_lesson_ids": waiting_lesson_ids,
                    },
                },
                trace_id=trace_id,
            )
        return None

    # semester level
    active_task = await db.scalar(
        select(AnalysisTask)
        .where(
            and_(
                AnalysisTask.course_id == request.course_id,
                AnalysisTask.task_kind == "semester_profile",
                AnalysisTask.status.in_([0, 1]),
            )
        )
        .order_by(AnalysisTask.updated_at.desc())
    )
    if active_task is not None:
        return QualityBaseResponse(
            code=20410,
            message=(
                "Data not ready: semester profile task is running, "
                f"course_id={request.course_id}, task_id={active_task.id}"
            ),
            data={
                "course_id": request.course_id,
                "report_level": request.report_level,
                "target_identifier": request.target_identifier,
                "module_name": request.module_name,
                "report_payload": None,
                "missing_summary": {
                    "active_task_id": active_task.id,
                    "active_task_status": active_task.status,
                },
            },
            trace_id=trace_id,
        )

    total_lessons = await db.scalar(
        select(func.count(Lesson.id)).where(Lesson.course_id == request.course_id)
    )
    if int(total_lessons or 0) == 0:
        return QualityBaseResponse(
            code=20410,
            message=(
                "Data not ready: course has no lesson data yet, "
                f"course_id={request.course_id}"
            ),
            data={
                "course_id": request.course_id,
                "report_level": request.report_level,
                "target_identifier": request.target_identifier,
                "module_name": request.module_name,
                "report_payload": None,
                "missing_summary": {"missing_weeks": []},
            },
            trace_id=trace_id,
        )

    missing_weeks_rows = (
        await db.execute(
            select(Lesson.week_number)
            .where(
                Lesson.course_id == request.course_id,
                Lesson.status != 3,
            )
            .distinct()
            .order_by(Lesson.week_number.asc())
        )
    ).scalars().all()
    if missing_weeks_rows:
        return QualityBaseResponse(
            code=20410,
            message=(
                "Data not ready: lessons in target scope are not fully analyzed, "
                f"course_id={request.course_id}"
            ),
            data={
                "course_id": request.course_id,
                "report_level": request.report_level,
                "target_identifier": request.target_identifier,
                "module_name": request.module_name,
                "report_payload": None,
                "missing_summary": {"missing_weeks": [int(w) for w in missing_weeks_rows]},
            },
            trace_id=trace_id,
        )
    return None


def _fmt_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat() + "Z"


def _resp_body(code: int, message: str, data: Dict[str, Any] | None, trace_id: str) -> Dict[str, Any]:
    return QualityBaseResponse(
        code=code,
        message=message,
        data=data,
        trace_id=trace_id,
    ).model_dump()
