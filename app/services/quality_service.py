"""质量画像模块最小实现服务。"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import json_repair
from openai import AsyncOpenAI
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
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
from app.prompts.activity_mix import (
    ACTIVITY_CLASSIFY_OUTPUT_SCHEMA,
    ACTIVITY_CLASSIFY_SYSTEM,
    ACTIVITY_CLASSIFY_USER_TEMPLATE,
    ACTIVITY_VERIFY_OUTPUT_SCHEMA,
    ACTIVITY_VERIFY_SYSTEM,
    ACTIVITY_VERIFY_USER_TEMPLATE,
)
from app.prompts.bloom_v2 import (
    BLOOM_INTERPRET_OUTPUT_SCHEMA,
    BLOOM_INTERPRET_SYSTEM,
    BLOOM_INTERPRET_USER_TEMPLATE,
    OCR_BLOOM_CALIBRATE_SYSTEM,
    OCR_BLOOM_CALIBRATE_USER_TEMPLATE,
    OCR_BLOOM_OUTPUT_SCHEMA,
    OCR_BLOOM_SYSTEM,
    OCR_BLOOM_USER_TEMPLATE,
    OCR_CLEAN_OUTPUT_SCHEMA,
    OCR_CLEAN_SYSTEM,
    OCR_CLEAN_USER_TEMPLATE,
    TEACHER_BLOOM_OUTPUT_SCHEMA,
    TEACHER_BLOOM_SYSTEM,
    TEACHER_BLOOM_USER_TEMPLATE,
    TEACHER_QUESTION_JUDGE_SYSTEM,
    TEACHER_QUESTION_JUDGE_USER_TEMPLATE,
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
    "teaching_activity_mix",
    "pace_challenge",
    "ideology_innovation",
    "atmosphere",
}

_LLM_CLIENT: Optional[AsyncOpenAI] = None
_OCR_NOISE_TOKENS = [
    "爱奇艺", "QQ影音", "腾讯视频", "微信", "回收站", "此电脑", "完美解码",
    "WPS", "Chrome", "Edge", "火绒", "SRun3K", "360", "超星直播", "B站",
    "bilibili", "窗口", "最小化", "最大化", "关闭", "播放", "暂停", "进度条",
]
_MAX_TEACHER_QUESTION_CANDIDATES = 24
_TEACHER_JUDGE_CONCURRENCY = 6
_ACTIVITY_WINDOW_SEC = 45
_ACTIVITY_MIN_WINDOW_SEC = 20
_ACTIVITY_PASS1_BATCH_SIZE = 10
_ACTIVITY_PASS2_BATCH_SIZE = 10
_ACTIVITY_PASS_CONCURRENCY = 4
_ACTIVITY_MAX_VERIFY_SEGMENTS = 20
_ACTIVITY_TYPES = {
    "theory_lecture",
    "case_discussion",
    "teacher_student_interaction",
    "experiment_explanation",
}
_ACTIVITY_LABELS_ZH = {
    "theory_lecture": "理论讲授",
    "case_discussion": "案例探讨",
    "teacher_student_interaction": "师生互动",
    "experiment_explanation": "实验讲解",
}


def _get_llm_client() -> AsyncOpenAI:
    global _LLM_CLIENT
    if _LLM_CLIENT is None:
        settings = get_settings()
        _LLM_CLIENT = AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
        )
    return _LLM_CLIENT


def _chunked(items: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    if size <= 0:
        return [items]
    return [items[i:i + size] for i in range(0, len(items), size)]


def _normalize_text(text: str) -> str:
    t = str(text or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t


def _normalize_distribution(dist: Dict[str, float]) -> Dict[str, int]:
    keys = ["l1", "l2", "l3", "l4", "l5", "l6"]
    raw = [max(0.0, float(dist.get(k, 0.0))) for k in keys]
    total = sum(raw)
    if total <= 0:
        return {"l1": 20, "l2": 20, "l3": 20, "l4": 20, "l5": 10, "l6": 10}

    scaled = [v * 100.0 / total for v in raw]
    ints = [int(v) for v in scaled]
    remainder = 100 - sum(ints)
    # 将余数按小数部分从大到小分配，确保总和精确为100
    frac_order = sorted(range(len(keys)), key=lambda i: scaled[i] - ints[i], reverse=True)
    for i in range(abs(remainder)):
        idx = frac_order[i % len(keys)]
        if remainder > 0:
            ints[idx] += 1
        elif ints[idx] > 0:
            ints[idx] -= 1

    return {k: ints[i] for i, k in enumerate(keys)}


def _weighted_merge_distribution(
    teacher_dist: Dict[str, int],
    ocr_dist: Dict[str, int],
    teacher_weight: float,
    ocr_weight: float,
) -> Dict[str, int]:
    total = teacher_weight + ocr_weight
    if total <= 0:
        teacher_weight, ocr_weight = 0.6, 0.4
        total = 1.0

    w_t = teacher_weight / total
    w_o = ocr_weight / total
    merged = {
        k: w_t * float(teacher_dist.get(k, 0)) + w_o * float(ocr_dist.get(k, 0))
        for k in ("l1", "l2", "l3", "l4", "l5", "l6")
    }
    return _normalize_distribution(merged)


def _calc_bands(overall_dist: Dict[str, int]) -> Dict[str, int]:
    high = int(overall_dist.get("l5", 0)) + int(overall_dist.get("l6", 0))
    mid = int(overall_dist.get("l3", 0)) + int(overall_dist.get("l4", 0))
    low = int(overall_dist.get("l1", 0)) + int(overall_dist.get("l2", 0))
    return {"high": high, "mid": mid, "low": low}


def _pick_topic_hint(course_name: str, ocr_segments: List[Dict[str, Any]]) -> str:
    for seg in ocr_segments:
        content = _normalize_text(seg.get("ocr_content", ""))
        if len(content) >= 8:
            return content[:40]
    return f"{course_name}课堂讲授"


def _is_sentence_end(text: str) -> bool:
    return bool(re.search(r"[。！？?!]$", text))


def _is_question_sentence(text: str) -> bool:
    return bool(re.search(r"[？?]$", text))


def _merge_asr_to_sentences(asr_segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = sorted(asr_segments, key=lambda x: (_safe_float(x.get("bg")), _safe_float(x.get("ed"))))
    sentences: List[Dict[str, Any]] = []
    buf_texts: List[str] = []
    buf_roles: List[str] = []
    start_t: Optional[float] = None
    end_t: Optional[float] = None
    sid = 1

    def flush() -> None:
        nonlocal sid, buf_texts, buf_roles, start_t, end_t
        if not buf_texts:
            return
        role = "unknown"
        if buf_roles:
            freq: Dict[str, int] = {}
            for r in buf_roles:
                rr = str(r or "").strip().lower() or "unknown"
                freq[rr] = freq.get(rr, 0) + 1
            role = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[0][0]
        text = "".join(buf_texts).strip()
        if text:
            sentences.append(
                {
                    "sentence_id": f"q{sid}",
                    "start": round(float(start_t or 0.0), 3),
                    "end": round(float(end_t or 0.0), 3),
                    "text": text,
                    "role": role,
                }
            )
            sid += 1
        buf_texts = []
        buf_roles = []
        start_t = None
        end_t = None

    for seg in ordered:
        text = _normalize_text(seg.get("text", ""))
        if not text:
            continue
        bg = _safe_float(seg.get("bg"), 0.0)
        ed = _safe_float(seg.get("ed"), bg)
        if start_t is None:
            start_t = bg
        if end_t is None:
            end_t = max(ed, bg)
        else:
            end_t = max(end_t, ed, bg)
        buf_texts.append(text)
        buf_roles.append(str(seg.get("role", "unknown")))
        if _is_sentence_end(text):
            flush()

    flush()
    return sentences


def _build_question_candidates(sentences: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for i, sent in enumerate(sentences):
        text = _normalize_text(sent.get("text", ""))
        if not _is_question_sentence(text):
            continue
        prev_sent = sentences[i - 1] if i > 0 else {}
        next_sent = sentences[i + 1] if i + 1 < len(sentences) else {}
        candidates.append(
            {
                "sentence_id": sent.get("sentence_id"),
                "start": sent.get("start", 0.0),
                "end": sent.get("end", 0.0),
                "candidate_question": text,
                "prev_sentence": _normalize_text(prev_sent.get("text", "")),
                "next_sentence": _normalize_text(next_sent.get("text", "")),
                "prev_role": prev_sent.get("role", "unknown"),
                "cur_role": sent.get("role", "unknown"),
                "next_role": next_sent.get("role", "unknown"),
            }
        )
    return candidates


def _heuristic_teacher_probability(question: str) -> float:
    q = _normalize_text(question)
    if not q:
        return 0.2
    score = 0.5
    teacher_cues = ["同学们", "大家", "思考", "想一想", "请分析", "请说明", "为什么", "如何", "能否"]
    student_cues = ["老师", "我想问", "请问老师", "我不太懂", "能再讲一下"]
    for cue in teacher_cues:
        if cue in q:
            score += 0.08
    for cue in student_cues:
        if cue in q:
            score -= 0.15
    return max(0.05, min(0.95, score))


def _heuristic_bloom_distribution(texts: List[str]) -> Dict[str, int]:
    score = {"l1": 1.0, "l2": 1.0, "l3": 1.0, "l4": 1.0, "l5": 1.0, "l6": 1.0}
    keyword_map = {
        "l1": ["是什么", "定义", "列举", "记住", "背诵"],
        "l2": ["解释", "说明", "为什么", "理解", "概念"],
        "l3": ["应用", "计算", "使用", "操作", "求解"],
        "l4": ["分析", "比较", "区别", "影响", "原因"],
        "l5": ["评价", "判断", "优缺点", "合理", "批判"],
        "l6": ["设计", "提出", "构建", "创新", "改进"],
    }
    for text in texts:
        t = _normalize_text(text)
        for level, words in keyword_map.items():
            for word in words:
                if word in t:
                    score[level] += 1.0
    return _normalize_distribution(score)


def _clean_ocr_text_rule(text: str) -> str:
    t = _normalize_text(text)
    for token in _OCR_NOISE_TOKENS:
        t = t.replace(token, " ")
    t = re.sub(r"\b\d{1,2}:\d{2}\b", " ", t)
    t = re.sub(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _get_near_asr_context(asr_segments: List[Dict[str, Any]], offset: int, window: int = 45) -> str:
    left = float(max(0, offset - window))
    right = float(offset + window)
    texts: List[str] = []
    for seg in asr_segments:
        bg = _safe_float(seg.get("bg"), 0.0)
        ed = _safe_float(seg.get("ed"), bg)
        if ed < left or bg > right:
            continue
        text = _normalize_text(seg.get("text", ""))
        if text:
            texts.append(text)
    return " ".join(texts)[:400]


async def _call_llm_json(
    *,
    system_prompt: str,
    user_prompt: str,
    response_schema: Optional[Dict[str, Any]] = None,
    max_tokens: int = 2048,
    temperature: float = 0.2,
) -> Optional[Dict[str, Any]]:
    settings = get_settings()
    client = _get_llm_client()
    base_kwargs: Dict[str, Any] = {
        "model": settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "timeout": 300,
    }

    async def _request_once(use_schema: bool) -> Optional[Dict[str, Any]]:
        kwargs = dict(base_kwargs)
        if use_schema and response_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "quality_schema",
                    "schema": response_schema,
                },
            }
        resp = await client.chat.completions.create(**kwargs)
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            return None
        return json.loads(json_repair.repair_json(content))

    try:
        return await _request_once(bool(response_schema))
    except Exception as e:
        if response_schema:
            logger.warning(f"[quality] LLM JSON schema模式失败，降级重试: {e}")
            try:
                return await _request_once(False)
            except Exception as e2:
                logger.warning(f"[quality] LLM JSON降级重试失败: {e2}")
                return None
        logger.warning(f"[quality] LLM JSON调用失败: {e}")
        return None


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


def _normalize_weight_pair(teacher_weight: float, ocr_weight: float) -> Tuple[float, float]:
    tw = max(0.0, _safe_float(teacher_weight, 0.6))
    ow = max(0.0, _safe_float(ocr_weight, 0.4))
    total = tw + ow
    if total <= 0:
        return 0.6, 0.4
    return tw / total, ow / total


def _validate_item_distribution(item: Dict[str, Any]) -> bool:
    keys = ["l1", "l2", "l3", "l4", "l5", "l6"]
    values: List[int] = []
    for key in keys:
        if key not in item:
            return False
        try:
            values.append(int(item[key]))
        except Exception:
            return False
    return sum(values) == 100 and all(v >= 0 for v in values)


def _aggregate_bloom_distribution(items: List[Dict[str, Any]], fallback_texts: List[str]) -> Dict[str, int]:
    if not items:
        return _heuristic_bloom_distribution(fallback_texts)

    score = {"l1": 0.0, "l2": 0.0, "l3": 0.0, "l4": 0.0, "l5": 0.0, "l6": 0.0}
    total_weight = 0.0
    for item in items:
        if not _validate_item_distribution(item):
            continue
        weight = max(0.0, _safe_float(item.get("weight", 1.0), 1.0))
        if weight <= 0:
            continue
        total_weight += weight
        for key in score.keys():
            score[key] += weight * _safe_float(item.get(key, 0.0), 0.0)

    if total_weight <= 0:
        return _heuristic_bloom_distribution(fallback_texts)

    avg_dist = {k: v / total_weight for k, v in score.items()}
    return _normalize_distribution(avg_dist)


async def _judge_teacher_questions(
    *,
    course_name: str,
    topic_hint: str,
    question_candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not question_candidates:
        return []

    # 候选压缩：优先保留更像老师提问的问句，避免逐条调用导致时延过高
    ranked: List[Dict[str, Any]] = []
    for candidate in question_candidates:
        h_prob = _heuristic_teacher_probability(candidate.get("candidate_question", ""))
        role = str(candidate.get("cur_role", "unknown")).strip().lower()
        role_boost = 0.15 if role == "teacher" else (-0.1 if role == "student" else 0.0)
        ranked.append(
            {
                "candidate": candidate,
                "rank_score": h_prob + role_boost,
                "h_prob": h_prob,
            }
        )
    ranked.sort(key=lambda x: x["rank_score"], reverse=True)
    candidates = [x["candidate"] for x in ranked[:_MAX_TEACHER_QUESTION_CANDIDATES]]

    semaphore = asyncio.Semaphore(_TEACHER_JUDGE_CONCURRENCY)

    async def _judge_one(candidate: Dict[str, Any]) -> Dict[str, Any]:
        user_prompt = TEACHER_QUESTION_JUDGE_USER_TEMPLATE.format(
            course_name=course_name,
            topic_hint=topic_hint,
            start=candidate.get("start", 0.0),
            end=candidate.get("end", 0.0),
            prev_sentence=candidate.get("prev_sentence", ""),
            candidate_question=candidate.get("candidate_question", ""),
            next_sentence=candidate.get("next_sentence", ""),
            prev_role=candidate.get("prev_role", "unknown"),
            cur_role=candidate.get("cur_role", "unknown"),
            next_role=candidate.get("next_role", "unknown"),
        )

        async with semaphore:
            try:
                resp = await asyncio.wait_for(
                    _call_llm_json(
                        system_prompt=TEACHER_QUESTION_JUDGE_SYSTEM,
                        user_prompt=user_prompt,
                        response_schema=None,
                        max_tokens=512,
                        temperature=0.1,
                    ),
                    timeout=45,
                )
            except asyncio.TimeoutError:
                resp = None

        teacher_probability = _heuristic_teacher_probability(candidate.get("candidate_question", ""))
        confidence = 0.6
        speaker = "teacher" if teacher_probability >= 0.6 else "student"
        reason = "规则估计结果"
        normalized_question = candidate.get("candidate_question", "")

        if isinstance(resp, dict):
            teacher_probability = max(
                0.0,
                min(1.0, _safe_float(resp.get("teacher_probability"), teacher_probability)),
            )
            confidence = max(0.0, min(1.0, _safe_float(resp.get("confidence"), confidence)))
            speaker_resp = str(resp.get("speaker", speaker)).strip().lower()
            if speaker_resp in {"teacher", "student", "unknown"}:
                speaker = speaker_resp
            reason = _normalize_text(resp.get("reason", reason))[:30] or reason
            normalized_question = _normalize_text(resp.get("normalized_question", normalized_question)) or normalized_question

        return {
            "sentence_id": candidate.get("sentence_id"),
            "start": candidate.get("start", 0.0),
            "end": candidate.get("end", 0.0),
            "question": normalized_question,
            "speaker": speaker,
            "teacher_probability": teacher_probability,
            "confidence": confidence,
            "reason": reason,
        }

    judged = await asyncio.gather(*[_judge_one(candidate) for candidate in candidates])

    selected = [
        item
        for item in judged
        if item["speaker"] == "teacher"
        and item["teacher_probability"] >= 0.65
        and item["confidence"] >= 0.6
    ]
    # 如果严格筛选为空，则退化到概率最高的若干候选，避免全空
    if not selected:
        sorted_candidates = sorted(
            judged,
            key=lambda x: (x.get("teacher_probability", 0.0), x.get("confidence", 0.0)),
            reverse=True,
        )
        selected = [x for x in sorted_candidates[:5] if x.get("teacher_probability", 0.0) >= 0.5]
    return selected


async def _classify_teacher_bloom(
    *,
    course_name: str,
    teacher_questions: List[Dict[str, Any]],
) -> Tuple[Dict[str, int], List[Dict[str, Any]]]:
    if not teacher_questions:
        return _heuristic_bloom_distribution([]), []

    payload = [
        {
            "sentence_id": item["sentence_id"],
            "question": item["question"],
            "start": item["start"],
            "end": item["end"],
            "teacher_probability": item["teacher_probability"],
            "confidence": item["confidence"],
        }
        for item in teacher_questions
    ]
    user_prompt = TEACHER_BLOOM_USER_TEMPLATE.format(
        course_name=course_name,
        questions_json=json.dumps(payload, ensure_ascii=False),
    )

    resp = await _call_llm_json(
        system_prompt=TEACHER_BLOOM_SYSTEM,
        user_prompt=user_prompt,
        response_schema=TEACHER_BLOOM_OUTPUT_SCHEMA,
        max_tokens=2048,
        temperature=0.1,
    )

    result_items: List[Dict[str, Any]] = []
    item_map = {item["sentence_id"]: item for item in teacher_questions}
    if isinstance(resp, dict) and isinstance(resp.get("items"), list):
        for item in resp["items"]:
            sid = str(item.get("sentence_id", "")).strip()
            if sid not in item_map or not _validate_item_distribution(item):
                continue
            origin = item_map[sid]
            weight = max(
                0.01,
                _safe_float(origin.get("teacher_probability"), 0.0) * _safe_float(item.get("confidence"), 0.0),
            )
            result_items.append(
                {
                    "sentence_id": sid,
                    "start": origin.get("start", 0.0),
                    "end": origin.get("end", 0.0),
                    "question": origin.get("question", ""),
                    "teacher_probability": max(
                        0.0,
                        min(1.0, _safe_float(origin.get("teacher_probability"), 0.0)),
                    ),
                    "l1": int(item["l1"]),
                    "l2": int(item["l2"]),
                    "l3": int(item["l3"]),
                    "l4": int(item["l4"]),
                    "l5": int(item["l5"]),
                    "l6": int(item["l6"]),
                    "confidence": max(0.0, min(1.0, _safe_float(item.get("confidence"), 0.0))),
                    "evidence_text": _normalize_text(item.get("evidence_text", origin.get("question", ""))),
                    "weight": weight,
                }
            )

    if not result_items:
        fallback_dist = _heuristic_bloom_distribution([x["question"] for x in teacher_questions])
        return fallback_dist, []

    teacher_dist = _aggregate_bloom_distribution(result_items, [x["question"] for x in teacher_questions])
    return teacher_dist, result_items


async def _clean_ocr_segments(
    *,
    course_name: str,
    asr_segments: List[Dict[str, Any]],
    ocr_segments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not ocr_segments:
        return []

    candidates: List[Dict[str, Any]] = []
    for idx, seg in enumerate(sorted(ocr_segments, key=lambda x: int(x.get("time_offset", 0))), start=1):
        raw_content = _normalize_text(seg.get("ocr_content", ""))
        if not raw_content:
            continue
        cleaned_content = _clean_ocr_text_rule(raw_content)
        if len(cleaned_content) < 4:
            continue
        clean_keywords = [_normalize_text(k) for k in (seg.get("ocr_keywords") or []) if _normalize_text(k)]
        near_context = _get_near_asr_context(asr_segments, int(seg.get("time_offset", 0)))
        candidates.append(
            {
                "ocr_id": f"o{idx}",
                "time_offset": int(seg.get("time_offset", 0)),
                "page_num": int(seg.get("page_num", 0)),
                "ocr_content": cleaned_content,
                "ocr_keywords": clean_keywords[:30],
                "near_asr_context": near_context,
            }
        )

    if not candidates:
        return []

    clean_results: Dict[str, Dict[str, Any]] = {}
    for batch in _chunked(candidates, 20):
        user_prompt = OCR_CLEAN_USER_TEMPLATE.format(
            course_name=course_name,
            ocr_items_json=json.dumps(batch, ensure_ascii=False),
        )
        resp = await _call_llm_json(
            system_prompt=OCR_CLEAN_SYSTEM,
            user_prompt=user_prompt,
            response_schema=OCR_CLEAN_OUTPUT_SCHEMA,
            max_tokens=2500,
            temperature=0.1,
        )

        if isinstance(resp, dict) and isinstance(resp.get("items"), list):
            for item in resp["items"]:
                ocr_id = str(item.get("ocr_id", "")).strip()
                if not ocr_id:
                    continue
                clean_results[ocr_id] = item

    kept: List[Dict[str, Any]] = []
    for item in candidates:
        ocr_id = item["ocr_id"]
        cleaned = clean_results.get(ocr_id)
        if not isinstance(cleaned, dict):
            kept.append(
                {
                    **item,
                    "keep": True,
                    "cleaned_content": item["ocr_content"],
                    "cleaned_keywords": item["ocr_keywords"],
                    "relevance_score": 0.6,
                    "noise_tags": [],
                }
            )
            continue

        keep = bool(cleaned.get("keep", True))
        cleaned_content = _normalize_text(cleaned.get("cleaned_content", item["ocr_content"]))
        cleaned_keywords = [
            _normalize_text(k) for k in (cleaned.get("cleaned_keywords") or []) if _normalize_text(k)
        ]
        relevance_score = max(0.0, min(1.0, _safe_float(cleaned.get("relevance_score"), 0.6)))
        noise_tags = [str(x) for x in (cleaned.get("noise_tags") or []) if str(x).strip()]
        if keep and cleaned_content:
            kept.append(
                {
                    **item,
                    "keep": keep,
                    "cleaned_content": cleaned_content,
                    "cleaned_keywords": cleaned_keywords[:30],
                    "relevance_score": relevance_score,
                    "noise_tags": noise_tags[:10],
                }
            )
    return kept


def _ocr_feature_text(source: Dict[str, Any], item: Dict[str, Any]) -> str:
    keywords = " ".join(source.get("cleaned_keywords") or [])
    parts = [
        _normalize_text(source.get("cleaned_content", "")),
        _normalize_text(source.get("near_asr_context", "")),
        _normalize_text(keywords),
        _normalize_text(item.get("evidence_text", "")),
    ]
    return _normalize_text(" ".join([p for p in parts if p]))[:2000]


def _text_has_any(text: str, keywords: List[str]) -> bool:
    if not text:
        return False
    return any(kw in text for kw in keywords)


def _ocr_has_innovation_signal(text: str) -> bool:
    innovation_keywords = [
        "创新",
        "设计",
        "提出",
        "新方案",
        "改进",
        "优化",
        "开放任务",
        "自主建模",
        "发明",
        "方案比较后重构",
    ]
    return _text_has_any(text, innovation_keywords)


def _ocr_has_evaluation_signal(text: str) -> bool:
    eval_keywords = [
        "评价",
        "判断",
        "优缺点",
        "合理性",
        "比较",
        "取舍",
        "标准",
        "证据支持",
        "反思",
    ]
    return _text_has_any(text, eval_keywords)


def _ocr_is_procedural_content(text: str) -> bool:
    procedural_keywords = [
        "积分",
        "方程",
        "推导",
        "步骤",
        "计算",
        "例题",
        "板书",
        "投影",
        "区域",
        "上下界",
        "变量范围",
        "坐标变换",
        "截面法",
        "分层结构",
    ]
    return _text_has_any(text, procedural_keywords)


def _need_ocr_extreme_recalibration(item: Dict[str, Any], source: Dict[str, Any]) -> bool:
    text = _ocr_feature_text(source, item)
    has_innovation = _ocr_has_innovation_signal(text)
    l6 = _safe_int(item.get("l6"), 0)
    l5 = _safe_int(item.get("l5"), 0)
    values = [_safe_int(item.get(k), 0) for k in ("l1", "l2", "l3", "l4", "l5", "l6")]
    max_bucket = max(values) if values else 0
    if max_bucket >= 90:
        return True
    if l6 >= 70:
        return True
    if not has_innovation and (l5 + l6 >= 85):
        return True
    return False


def _apply_ocr_distribution_rule_calibration(
    item: Dict[str, Any],
    source: Dict[str, Any],
) -> Dict[str, int]:
    keys = ["l1", "l2", "l3", "l4", "l5", "l6"]
    dist = {k: max(0.0, _safe_float(item.get(k), 0.0)) for k in keys}
    text = _ocr_feature_text(source, item)
    has_innovation = _ocr_has_innovation_signal(text)
    has_evaluation = _ocr_has_evaluation_signal(text)
    is_procedural = _ocr_is_procedural_content(text)

    # 极端塌缩先做保守混合，避免单级100%
    if max(dist.values()) >= 90:
        prior = {"l1": 10.0, "l2": 20.0, "l3": 28.0, "l4": 26.0, "l5": 11.0, "l6": 5.0}
        alpha = 0.25
        dist = {k: alpha * dist[k] + (1.0 - alpha) * prior[k] for k in keys}

    if not has_innovation and dist["l6"] > 15.0:
        excess = dist["l6"] - 15.0
        dist["l6"] = 15.0
        dist["l3"] += excess * 0.55
        dist["l4"] += excess * 0.45

    if not has_evaluation and dist["l5"] > 20.0:
        excess = dist["l5"] - 20.0
        dist["l5"] = 20.0
        dist["l3"] += excess * 0.45
        dist["l4"] += excess * 0.55

    if is_procedural:
        mid = dist["l3"] + dist["l4"]
        target_mid = 45.0
        if mid < target_mid:
            need = target_mid - mid
            for k in ["l6", "l5", "l1", "l2"]:
                floor = 5.0 if k in {"l1", "l2"} else 0.0
                avail = max(0.0, dist[k] - floor)
                take = min(avail, need)
                dist[k] -= take
                need -= take
                if need <= 0:
                    break
            added = (target_mid - mid) - max(0.0, need)
            dist["l3"] += added * 0.55
            dist["l4"] += added * 0.45

    return _normalize_distribution(dist)


async def _recalibrate_extreme_ocr_items(
    *,
    course_name: str,
    extreme_items: List[Dict[str, Any]],
    source_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    if not extreme_items:
        return {}

    payload: List[Dict[str, Any]] = []
    for item in extreme_items:
        source = source_map.get(item["ocr_id"], {})
        payload.append(
            {
                "ocr_id": item["ocr_id"],
                "cleaned_content": source.get("cleaned_content", ""),
                "cleaned_keywords": source.get("cleaned_keywords", []),
                "near_asr_context": source.get("near_asr_context", ""),
                "current_distribution": {
                    "l1": _safe_int(item.get("l1"), 0),
                    "l2": _safe_int(item.get("l2"), 0),
                    "l3": _safe_int(item.get("l3"), 0),
                    "l4": _safe_int(item.get("l4"), 0),
                    "l5": _safe_int(item.get("l5"), 0),
                    "l6": _safe_int(item.get("l6"), 0),
                },
                "current_evidence": item.get("evidence_text", ""),
            }
        )

    user_prompt = OCR_BLOOM_CALIBRATE_USER_TEMPLATE.format(
        course_name=course_name,
        calibrate_items_json=json.dumps(payload, ensure_ascii=False),
    )
    resp = await _call_llm_json(
        system_prompt=OCR_BLOOM_CALIBRATE_SYSTEM,
        user_prompt=user_prompt,
        response_schema=OCR_BLOOM_OUTPUT_SCHEMA,
        max_tokens=1800,
        temperature=0.05,
    )

    updates: Dict[str, Dict[str, Any]] = {}
    if isinstance(resp, dict) and isinstance(resp.get("items"), list):
        for item in resp["items"]:
            oid = str(item.get("ocr_id", "")).strip()
            if not oid:
                continue
            if not _validate_item_distribution(item):
                continue
            updates[oid] = {
                "l1": int(item["l1"]),
                "l2": int(item["l2"]),
                "l3": int(item["l3"]),
                "l4": int(item["l4"]),
                "l5": int(item["l5"]),
                "l6": int(item["l6"]),
                "confidence": max(0.0, min(1.0, _safe_float(item.get("confidence"), 0.6))),
                "evidence_text": _normalize_text(item.get("evidence_text", ""))[:120],
            }
    return updates


async def _classify_ocr_bloom(
    *,
    course_name: str,
    clean_ocr_segments: List[Dict[str, Any]],
) -> Tuple[Dict[str, int], List[Dict[str, Any]]]:
    if not clean_ocr_segments:
        return _heuristic_bloom_distribution([]), []

    payload = [
        {
            "ocr_id": item["ocr_id"],
            "time_offset": item["time_offset"],
            "cleaned_content": item["cleaned_content"],
            "cleaned_keywords": item["cleaned_keywords"],
            "near_asr_context": item["near_asr_context"],
            "relevance_score": item["relevance_score"],
        }
        for item in clean_ocr_segments
    ]
    user_prompt = OCR_BLOOM_USER_TEMPLATE.format(
        course_name=course_name,
        clean_ocr_items_json=json.dumps(payload, ensure_ascii=False),
    )
    resp = await _call_llm_json(
        system_prompt=OCR_BLOOM_SYSTEM,
        user_prompt=user_prompt,
        response_schema=OCR_BLOOM_OUTPUT_SCHEMA,
        max_tokens=2500,
        temperature=0.1,
    )

    item_map = {item["ocr_id"]: item for item in clean_ocr_segments}
    result_items: List[Dict[str, Any]] = []
    if isinstance(resp, dict) and isinstance(resp.get("items"), list):
        for item in resp["items"]:
            oid = str(item.get("ocr_id", "")).strip()
            if oid not in item_map or not _validate_item_distribution(item):
                continue
            source = item_map[oid]
            weight = max(
                0.01,
                _safe_float(source.get("relevance_score"), 0.0) * _safe_float(item.get("confidence"), 0.0),
            )
            result_items.append(
                {
                    "ocr_id": oid,
                    "time_offset": source.get("time_offset", 0),
                    "page_num": source.get("page_num", 0),
                    "cleaned_content": source.get("cleaned_content", ""),
                    "l1": int(item["l1"]),
                    "l2": int(item["l2"]),
                    "l3": int(item["l3"]),
                    "l4": int(item["l4"]),
                    "l5": int(item["l5"]),
                    "l6": int(item["l6"]),
                    "confidence": max(0.0, min(1.0, _safe_float(item.get("confidence"), 0.0))),
                    "evidence_text": _normalize_text(item.get("evidence_text", source.get("cleaned_content", ""))),
                    "weight": weight,
                }
            )

    if not result_items:
        fallback_dist = _heuristic_bloom_distribution([x.get("cleaned_content", "") for x in clean_ocr_segments])
        return fallback_dist, []

    # 先对极端分布片段做一次保守重判
    extreme_items = [
        item
        for item in result_items
        if _need_ocr_extreme_recalibration(item, item_map.get(item["ocr_id"], {}))
    ]
    if extreme_items:
        updates = await _recalibrate_extreme_ocr_items(
            course_name=course_name,
            extreme_items=extreme_items,
            source_map=item_map,
        )
        for item in result_items:
            upd = updates.get(item["ocr_id"])
            if not upd:
                continue
            item["l1"] = int(upd["l1"])
            item["l2"] = int(upd["l2"])
            item["l3"] = int(upd["l3"])
            item["l4"] = int(upd["l4"])
            item["l5"] = int(upd["l5"])
            item["l6"] = int(upd["l6"])
            item["confidence"] = min(
                _safe_float(item.get("confidence"), 0.6),
                _safe_float(upd.get("confidence"), 0.6),
            )
            if upd.get("evidence_text"):
                item["evidence_text"] = upd["evidence_text"]

    # 再做规则校准，避免L6塌缩
    for item in result_items:
        source = item_map.get(item["ocr_id"], {})
        calibrated = _apply_ocr_distribution_rule_calibration(item, source)
        item["l1"] = calibrated["l1"]
        item["l2"] = calibrated["l2"]
        item["l3"] = calibrated["l3"]
        item["l4"] = calibrated["l4"]
        item["l5"] = calibrated["l5"]
        item["l6"] = calibrated["l6"]

    ocr_dist = _aggregate_bloom_distribution(result_items, [x.get("cleaned_content", "") for x in clean_ocr_segments])
    return ocr_dist, result_items


async def _build_bloom_interpretation(
    *,
    course_name: str,
    topic_hint: str,
    teacher_dist: Dict[str, int],
    ocr_dist: Dict[str, int],
    overall_dist: Dict[str, int],
    bands: Dict[str, int],
    teacher_weight: float,
    ocr_weight: float,
) -> str:
    user_prompt = BLOOM_INTERPRET_USER_TEMPLATE.format(
        course_name=course_name,
        topic_hint=topic_hint,
        teacher_distribution=json.dumps(teacher_dist, ensure_ascii=False),
        ocr_distribution=json.dumps(ocr_dist, ensure_ascii=False),
        overall_distribution=json.dumps(overall_dist, ensure_ascii=False),
        bands=json.dumps(bands, ensure_ascii=False),
        teacher_weight=round(teacher_weight, 4),
        ocr_weight=round(ocr_weight, 4),
    )
    resp = await _call_llm_json(
        system_prompt=BLOOM_INTERPRET_SYSTEM,
        user_prompt=user_prompt,
        response_schema=BLOOM_INTERPRET_OUTPUT_SCHEMA,
        max_tokens=400,
        temperature=0.2,
    )
    if isinstance(resp, dict):
        text = _normalize_text(resp.get("ai_interpretation", ""))
        if text:
            return text
    return (
        f"本节课在{topic_hint}相关内容中，以中阶认知任务为主（{bands['mid']}%），"
        f"高阶认知占比{bands['high']}%。建议在后续环节增加评价与创造类问题，"
        "进一步提升学生高阶思维参与度。"
    )


async def _build_bloom_payload(
    *,
    course_name: str,
    asr_segments: List[Dict[str, Any]],
    ocr_segments: List[Dict[str, Any]],
    teacher_weight: float,
    ocr_weight: float,
) -> Dict[str, Any]:
    topic_hint = _pick_topic_hint(course_name, ocr_segments)
    merged_sentences = _merge_asr_to_sentences(asr_segments)
    question_candidates = _build_question_candidates(merged_sentences)
    teacher_questions = await _judge_teacher_questions(
        course_name=course_name,
        topic_hint=topic_hint,
        question_candidates=question_candidates,
    )

    teacher_dist, teacher_items = await _classify_teacher_bloom(
        course_name=course_name,
        teacher_questions=teacher_questions,
    )

    clean_ocr_segments = await _clean_ocr_segments(
        course_name=course_name,
        asr_segments=asr_segments,
        ocr_segments=ocr_segments,
    )
    ocr_dist, ocr_items = await _classify_ocr_bloom(
        course_name=course_name,
        clean_ocr_segments=clean_ocr_segments,
    )
    # 兜底规则校准：避免任何路径下出现 OCR Bloom 单级塌缩（如 L6=100）。
    if ocr_items:
        source_map = {seg.get("ocr_id"): seg for seg in clean_ocr_segments}
        for item in ocr_items:
            src = source_map.get(item.get("ocr_id"), {})
            calibrated = _apply_ocr_distribution_rule_calibration(item, src)
            item["l1"] = calibrated["l1"]
            item["l2"] = calibrated["l2"]
            item["l3"] = calibrated["l3"]
            item["l4"] = calibrated["l4"]
            item["l5"] = calibrated["l5"]
            item["l6"] = calibrated["l6"]
        ocr_dist = _aggregate_bloom_distribution(
            ocr_items,
            [x.get("cleaned_content", "") for x in clean_ocr_segments],
        )

    w_t, w_o = _normalize_weight_pair(teacher_weight, ocr_weight)
    overall_dist = _weighted_merge_distribution(teacher_dist, ocr_dist, w_t, w_o)
    bands = _calc_bands(overall_dist)
    interpretation = await _build_bloom_interpretation(
        course_name=course_name,
        topic_hint=topic_hint,
        teacher_dist=teacher_dist,
        ocr_dist=ocr_dist,
        overall_dist=overall_dist,
        bands=bands,
        teacher_weight=w_t,
        ocr_weight=w_o,
    )

    evidence_teacher = [
        {
            "sentence_id": item.get("sentence_id"),
            "start": item.get("start"),
            "end": item.get("end"),
            "question": item.get("question"),
            "confidence": round(_safe_float(item.get("confidence"), 0.0), 4),
            "teacher_probability": round(_safe_float(item.get("teacher_probability"), 0.0), 4),
            "bloom": {
                "l1": item.get("l1"),
                "l2": item.get("l2"),
                "l3": item.get("l3"),
                "l4": item.get("l4"),
                "l5": item.get("l5"),
                "l6": item.get("l6"),
            } if "l1" in item else None,
            "evidence_text": item.get("evidence_text"),
        }
        for item in teacher_items[:30]
    ]
    evidence_ocr = [
        {
            "ocr_id": item.get("ocr_id"),
            "time_offset": item.get("time_offset"),
            "page_num": item.get("page_num"),
            "content": item.get("cleaned_content"),
            "confidence": round(_safe_float(item.get("confidence"), 0.0), 4),
            "bloom": {
                "l1": item.get("l1"),
                "l2": item.get("l2"),
                "l3": item.get("l3"),
                "l4": item.get("l4"),
                "l5": item.get("l5"),
                "l6": item.get("l6"),
            } if "l1" in item else None,
            "evidence_text": item.get("evidence_text"),
        }
        for item in ocr_items[:30]
    ]

    return {
        "weights": {"teacher": round(w_t, 4), "ocr": round(w_o, 4)},
        "teacher_distribution": teacher_dist,
        "ocr_distribution": ocr_dist,
        "overall_distribution": overall_dist,
        "bands": bands,
        "evidence": {
            "teacher_questions": evidence_teacher,
            "ocr_fragments": evidence_ocr,
        },
        "ai_interpretation": interpretation,
    }


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def _normalize_activity_type(activity_type: str) -> str:
    t = str(activity_type or "").strip().lower()
    if t in _ACTIVITY_TYPES:
        return t
    return "theory_lecture"


def _estimate_lesson_duration_sec(
    asr_segments: List[Dict[str, Any]],
    ocr_segments: List[Dict[str, Any]],
) -> int:
    max_ed = max((_safe_float(seg.get("ed"), 0.0) for seg in asr_segments), default=0.0)
    min_bg = min((_safe_float(seg.get("bg"), 0.0) for seg in asr_segments), default=0.0)
    asr_duration = max(0.0, max_ed - min_bg)
    max_ocr_offset = float(max((_safe_int(seg.get("time_offset"), 0) for seg in ocr_segments), default=0))
    # OCR通常是切片时间，末尾补一段尾部缓冲
    duration = max(asr_duration, max_ocr_offset + 30.0)
    if duration <= 0:
        duration = float(_ACTIVITY_WINDOW_SEC)
    return max(_ACTIVITY_MIN_WINDOW_SEC, int(round(duration)))


def _build_activity_windows(
    *,
    asr_segments: List[Dict[str, Any]],
    ocr_segments: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    duration = _estimate_lesson_duration_sec(asr_segments, ocr_segments)
    if duration >= 2400:
        window_size = 90
    elif duration >= 1500:
        window_size = 75
    elif duration >= 900:
        window_size = 60
    else:
        window_size = _ACTIVITY_WINDOW_SEC
        if duration <= _ACTIVITY_WINDOW_SEC * 8:
            window_size = max(_ACTIVITY_MIN_WINDOW_SEC, int(round(duration / 8.0)))
    window_size = max(_ACTIVITY_MIN_WINDOW_SEC, window_size)

    windows: List[Dict[str, Any]] = []
    start = 0
    idx = 1
    while start < duration:
        end = min(duration, start + window_size)
        if end - start < _ACTIVITY_MIN_WINDOW_SEC and windows:
            windows[-1]["end_sec"] = duration
            break

        asr_hits: List[Dict[str, Any]] = []
        teacher_texts: List[str] = []
        student_texts: List[str] = []
        all_texts: List[str] = []
        question_count = 0
        teacher_speaking = 0.0
        student_speaking = 0.0
        for seg in asr_segments:
            bg = _safe_float(seg.get("bg"), 0.0)
            ed = _safe_float(seg.get("ed"), bg)
            if ed <= start or bg >= end:
                continue
            overlap = max(0.0, min(float(end), ed) - max(float(start), bg))
            text = _normalize_text(seg.get("text", ""))
            if text:
                all_texts.append(text)
                question_count += text.count("?") + text.count("？")
            role = str(seg.get("role", "unknown")).strip().lower()
            if role == "teacher":
                teacher_speaking += overlap
                if text:
                    teacher_texts.append(text)
            elif role == "student":
                student_speaking += overlap
                if text:
                    student_texts.append(text)
            asr_hits.append(seg)

        ocr_hits: List[Dict[str, Any]] = []
        ocr_texts: List[str] = []
        ocr_keywords: List[str] = []
        for seg in ocr_segments:
            offset = _safe_int(seg.get("time_offset"), 0)
            if offset < start - 15 or offset > end + 15:
                continue
            ocr_hits.append(seg)
            content = _clean_ocr_text_rule(seg.get("ocr_content", ""))
            if content:
                ocr_texts.append(content)
            for kw in seg.get("ocr_keywords") or []:
                n_kw = _normalize_text(kw)
                if n_kw:
                    ocr_keywords.append(n_kw)

        # 去重保序
        uniq_keywords: List[str] = []
        seen = set()
        for kw in ocr_keywords:
            if kw in seen:
                continue
            seen.add(kw)
            uniq_keywords.append(kw)

        asr_text = " ".join(all_texts)[:1000]
        windows.append(
            {
                "segment_id": f"s{idx}",
                "start_sec": int(start),
                "end_sec": int(end),
                "asr_text": asr_text,
                "teacher_text": " ".join(teacher_texts)[:400],
                "student_text": " ".join(student_texts)[:400],
                "question_count": int(question_count),
                "teacher_speaking_sec": round(teacher_speaking, 3),
                "student_speaking_sec": round(student_speaking, 3),
                "ocr_text": " ".join(ocr_texts[:3])[:450],
                "ocr_keywords": uniq_keywords[:16],
                "asr_count": len(asr_hits),
                "ocr_count": len(ocr_hits),
            }
        )
        idx += 1
        start = end

    if windows:
        windows[0]["start_sec"] = 0
        windows[-1]["end_sec"] = duration
    return windows, duration


def _heuristic_activity(window: Dict[str, Any]) -> Tuple[str, float, str]:
    combined = " ".join(
        [
            _normalize_text(window.get("teacher_text", "")),
            _normalize_text(window.get("student_text", "")),
            _normalize_text(window.get("asr_text", "")),
            _normalize_text(window.get("ocr_text", "")),
            " ".join(window.get("ocr_keywords") or []),
        ]
    )
    question_count = _safe_int(window.get("question_count"), 0)
    teacher_speaking = _safe_float(window.get("teacher_speaking_sec"), 0.0)
    student_speaking = _safe_float(window.get("student_speaking_sec"), 0.0)

    scores: Dict[str, float] = {
        "theory_lecture": 1.0,
        "case_discussion": 0.8,
        "teacher_student_interaction": 0.8,
        "experiment_explanation": 0.8,
    }

    experiment_keywords = ["实验", "演示", "操作", "步骤", "观察", "显微", "实习", "测试", "结果"]
    case_keywords = ["案例", "例如", "比如", "实例", "场景", "情境", "讨论这个", "分析这个"]
    theory_keywords = ["定义", "概念", "原理", "性质", "结构", "分类", "方法", "理论"]
    interaction_keywords = ["同学们", "谁来", "请回答", "你觉得", "为什么", "怎么看", "有没有问题"]

    for kw in experiment_keywords:
        if kw in combined:
            scores["experiment_explanation"] += 0.8
    for kw in case_keywords:
        if kw in combined:
            scores["case_discussion"] += 0.8
    for kw in theory_keywords:
        if kw in combined:
            scores["theory_lecture"] += 0.35
    for kw in interaction_keywords:
        if kw in combined:
            scores["teacher_student_interaction"] += 0.6

    if question_count > 0:
        scores["teacher_student_interaction"] += min(1.4, question_count * 0.35)
    if student_speaking >= 8.0:
        scores["teacher_student_interaction"] += 0.9
    if teacher_speaking >= 6.0 and student_speaking >= 4.0:
        scores["teacher_student_interaction"] += 0.6
    if teacher_speaking >= 12.0 and student_speaking <= 2.0:
        scores["theory_lecture"] += 0.7

    activity_type = max(scores.items(), key=lambda kv: kv[1])[0]
    sorted_scores = sorted(scores.values(), reverse=True)
    margin = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else sorted_scores[0]
    confidence = max(0.45, min(0.95, 0.55 + margin * 0.18))
    evidence = _normalize_text(window.get("teacher_text") or window.get("asr_text") or window.get("ocr_text"))[:40]
    if not evidence:
        evidence = "片段语义特征不足，按启发式判定"
    return activity_type, round(confidence, 4), evidence


def _validate_activity_item(item: Dict[str, Any]) -> bool:
    segment_id = str(item.get("segment_id", "")).strip()
    activity_type = _normalize_activity_type(item.get("activity_type", ""))
    confidence = _safe_float(item.get("confidence"), -1)
    return bool(segment_id) and activity_type in _ACTIVITY_TYPES and 0 <= confidence <= 1


async def _classify_activity_pass1(
    *,
    course_name: str,
    windows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not windows:
        return []

    semaphore = asyncio.Semaphore(_ACTIVITY_PASS_CONCURRENCY)

    async def _classify_batch(batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        payload = [
            {
                "segment_id": item["segment_id"],
                "start_sec": item["start_sec"],
                "end_sec": item["end_sec"],
                "teacher_text": item["teacher_text"][:260],
                "student_text": item["student_text"][:260],
                "asr_text": item["asr_text"][:300],
                "ocr_text": item["ocr_text"][:240],
                "ocr_keywords": item["ocr_keywords"][:10],
                "question_count": item["question_count"],
            }
            for item in batch
        ]
        user_prompt = ACTIVITY_CLASSIFY_USER_TEMPLATE.format(
            course_name=course_name,
            segment_items_json=json.dumps(payload, ensure_ascii=False),
        )
        async with semaphore:
            resp = await _call_llm_json(
                system_prompt=ACTIVITY_CLASSIFY_SYSTEM,
                user_prompt=user_prompt,
                response_schema=ACTIVITY_CLASSIFY_OUTPUT_SCHEMA,
                max_tokens=1800,
                temperature=0.1,
            )
        result_by_id: Dict[str, Dict[str, Any]] = {}
        if isinstance(resp, dict) and isinstance(resp.get("items"), list):
            for item in resp["items"]:
                if not _validate_activity_item(item):
                    continue
                seg_id = str(item.get("segment_id")).strip()
                result_by_id[seg_id] = {
                    "segment_id": seg_id,
                    "activity_type": _normalize_activity_type(item.get("activity_type", "")),
                    "confidence": round(max(0.0, min(1.0, _safe_float(item.get("confidence"), 0.6))), 4),
                    "evidence_text": _normalize_text(item.get("evidence_text", ""))[:40],
                }

        outputs: List[Dict[str, Any]] = []
        for w in batch:
            got = result_by_id.get(w["segment_id"])
            if got:
                outputs.append(got)
            else:
                t, c, e = _heuristic_activity(w)
                outputs.append(
                    {
                        "segment_id": w["segment_id"],
                        "activity_type": t,
                        "confidence": c,
                        "evidence_text": e,
                    }
                )
        return outputs

    tasks = [
        _classify_batch(batch)
        for batch in _chunked(windows, _ACTIVITY_PASS1_BATCH_SIZE)
    ]
    batches = await asyncio.gather(*tasks)
    flattened = [x for b in batches for x in b]
    by_id = {x["segment_id"]: x for x in flattened}
    return [by_id[w["segment_id"]] for w in windows if w["segment_id"] in by_id]


def _smooth_activity_labels(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(items) < 3:
        return items
    out = [dict(x) for x in items]
    for i in range(1, len(out) - 1):
        prev_t = out[i - 1]["activity_type"]
        cur_t = out[i]["activity_type"]
        next_t = out[i + 1]["activity_type"]
        cur_c = _safe_float(out[i].get("confidence"), 0.0)
        if prev_t == next_t and cur_t != prev_t and cur_c < 0.62:
            out[i]["activity_type"] = prev_t
            out[i]["confidence"] = round(max(0.5, cur_c), 4)
            out[i]["evidence_text"] = "邻域平滑修正"
    return out


def _select_verify_segments(
    windows: List[Dict[str, Any]],
    pass1_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not pass1_items:
        return []
    candidates: List[Tuple[int, float, int, Dict[str, Any]]] = []
    by_id = {w["segment_id"]: w for w in windows}
    for i, item in enumerate(pass1_items):
        sid = item["segment_id"]
        conf = _safe_float(item.get("confidence"), 0.0)
        cur = item["activity_type"]
        prev_t = pass1_items[i - 1]["activity_type"] if i > 0 else cur
        next_t = pass1_items[i + 1]["activity_type"] if i + 1 < len(pass1_items) else cur
        boundary = (cur != prev_t) or (cur != next_t)
        if conf < 0.62 or boundary:
            w = by_id.get(sid)
            if not w:
                continue
            priority = 0
            if boundary:
                priority += 1
            if conf < 0.62:
                priority += 1
            if conf < 0.55:
                priority += 1
            candidates.append(
                (
                    priority,
                    -conf,
                    int(w["start_sec"]),
                    {
                        "segment_id": sid,
                        "start_sec": w["start_sec"],
                        "end_sec": w["end_sec"],
                        "current_activity_type": cur,
                        "current_confidence": conf,
                        "teacher_text": w["teacher_text"][:220],
                        "student_text": w["student_text"][:220],
                        "asr_text": w["asr_text"][:260],
                        "ocr_text": w["ocr_text"][:220],
                        "ocr_keywords": w["ocr_keywords"][:10],
                    },
                )
            )
    if not candidates:
        return []
    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))
    selected = [x[3] for x in candidates[:_ACTIVITY_MAX_VERIFY_SEGMENTS]]
    selected.sort(key=lambda x: int(x["start_sec"]))
    return selected


async def _verify_activity_pass2(
    *,
    course_name: str,
    verify_segments: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    if not verify_segments:
        return {}
    semaphore = asyncio.Semaphore(_ACTIVITY_PASS_CONCURRENCY)

    async def _verify_batch(batch: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        user_prompt = ACTIVITY_VERIFY_USER_TEMPLATE.format(
            course_name=course_name,
            verify_items_json=json.dumps(batch, ensure_ascii=False),
        )
        async with semaphore:
            resp = await _call_llm_json(
                system_prompt=ACTIVITY_VERIFY_SYSTEM,
                user_prompt=user_prompt,
                response_schema=ACTIVITY_VERIFY_OUTPUT_SCHEMA,
                max_tokens=1800,
                temperature=0.1,
            )
        updates: Dict[str, Dict[str, Any]] = {}
        if isinstance(resp, dict) and isinstance(resp.get("items"), list):
            for item in resp["items"]:
                sid = str(item.get("segment_id", "")).strip()
                if not sid:
                    continue
                final_type = _normalize_activity_type(item.get("final_activity_type", ""))
                conf = max(0.0, min(1.0, _safe_float(item.get("confidence"), 0.6)))
                keep = bool(item.get("keep_current_label", True))
                reason = _normalize_text(item.get("reason", "二次复核"))[:30] or "二次复核"
                updates[sid] = {
                    "activity_type": final_type,
                    "confidence": round(conf, 4),
                    "keep_current_label": keep,
                    "evidence_text": reason,
                }
        return updates

    batches = _chunked(verify_segments, _ACTIVITY_PASS2_BATCH_SIZE)
    parts = await asyncio.gather(*[_verify_batch(batch) for batch in batches])
    merged: Dict[str, Dict[str, Any]] = {}
    for p in parts:
        merged.update(p)
    return merged


def _merge_activity_timeline(
    windows: List[Dict[str, Any]],
    final_items: List[Dict[str, Any]],
    duration_sec: int,
) -> List[Dict[str, Any]]:
    if not windows or not final_items:
        return []
    by_id = {x["segment_id"]: x for x in final_items}
    rows: List[Dict[str, Any]] = []
    for w in windows:
        x = by_id.get(w["segment_id"])
        if not x:
            t, c, e = _heuristic_activity(w)
            x = {"activity_type": t, "confidence": c, "evidence_text": e}
        rows.append(
            {
                "start_sec": int(w["start_sec"]),
                "end_sec": int(w["end_sec"]),
                "activity_type": _normalize_activity_type(x.get("activity_type", "")),
                "confidence": round(max(0.0, min(1.0, _safe_float(x.get("confidence"), 0.6))), 4),
                "evidence_text": _normalize_text(x.get("evidence_text", ""))[:40],
            }
        )

    rows.sort(key=lambda x: x["start_sec"])
    merged: List[Dict[str, Any]] = []
    for row in rows:
        if not merged:
            merged.append(dict(row))
            continue
        last = merged[-1]
        if row["activity_type"] == last["activity_type"] and row["start_sec"] <= last["end_sec"]:
            last["end_sec"] = max(last["end_sec"], row["end_sec"])
            last["confidence"] = round((last["confidence"] + row["confidence"]) / 2.0, 4)
            if not last.get("evidence_text"):
                last["evidence_text"] = row.get("evidence_text", "")
            continue
        merged.append(dict(row))

    # 连续性修复：强制无缝对齐，并覆盖完整时长
    if merged:
        merged[0]["start_sec"] = 0
        for i in range(1, len(merged)):
            merged[i]["start_sec"] = merged[i - 1]["end_sec"]
            if merged[i]["end_sec"] < merged[i]["start_sec"]:
                merged[i]["end_sec"] = merged[i]["start_sec"]
        merged[-1]["end_sec"] = duration_sec

    # 去除0时长段
    merged = [x for x in merged if x["end_sec"] > x["start_sec"]]
    return merged


def _build_activity_distribution(
    timeline: List[Dict[str, Any]],
    duration_sec: int,
) -> List[Dict[str, Any]]:
    acc = {
        "theory_lecture": 0,
        "case_discussion": 0,
        "teacher_student_interaction": 0,
        "experiment_explanation": 0,
    }
    for item in timeline:
        t = _normalize_activity_type(item.get("activity_type", ""))
        d = max(0, _safe_int(item.get("end_sec"), 0) - _safe_int(item.get("start_sec"), 0))
        acc[t] += d

    total = max(1, duration_sec)
    result: List[Dict[str, Any]] = []
    order = [
        "theory_lecture",
        "case_discussion",
        "teacher_student_interaction",
        "experiment_explanation",
    ]
    for t in order:
        d = int(acc[t])
        pct = round(d * 100.0 / total, 2)
        result.append(
            {
                "activity_type": t,
                "activity_label": _ACTIVITY_LABELS_ZH[t],
                "duration_sec": d,
                "duration_text": f"{d // 60}分{d % 60:02d}秒",
                "percent": pct,
            }
        )
    return result


def _build_activity_quality_checks(
    timeline: List[Dict[str, Any]],
    duration_sec: int,
) -> Dict[str, Any]:
    if duration_sec <= 0:
        return {
            "is_continuous": True,
            "coverage_ratio": 0.0,
            "total_duration_sec": 0,
            "timeline_total_sec": 0,
            "issues": [],
        }
    timeline_total = sum(max(0, _safe_int(x.get("end_sec"), 0) - _safe_int(x.get("start_sec"), 0)) for x in timeline)
    coverage_ratio = round(min(1.0, max(0.0, timeline_total / float(duration_sec))), 4)
    issues: List[str] = []
    for i in range(1, len(timeline)):
        if _safe_int(timeline[i].get("start_sec"), 0) != _safe_int(timeline[i - 1].get("end_sec"), 0):
            issues.append(f"segment_gap_or_overlap_at_{i}")
    if timeline and _safe_int(timeline[0].get("start_sec"), 0) != 0:
        issues.append("start_not_zero")
    if timeline and _safe_int(timeline[-1].get("end_sec"), 0) != duration_sec:
        issues.append("end_not_duration")
    is_continuous = len(issues) == 0
    return {
        "is_continuous": is_continuous,
        "coverage_ratio": coverage_ratio,
        "total_duration_sec": duration_sec,
        "timeline_total_sec": timeline_total,
        "issues": issues,
    }


async def _build_activity_mix_payload(
    *,
    course_name: str,
    asr_segments: List[Dict[str, Any]],
    ocr_segments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    windows, duration_sec = _build_activity_windows(asr_segments=asr_segments, ocr_segments=ocr_segments)
    if not windows:
        empty_distribution = _build_activity_distribution([], duration_sec)
        return {
            "timeline": [],
            "distribution": empty_distribution,
            "quality_checks": _build_activity_quality_checks([], duration_sec),
            "meta": {
                "window_size_sec": _ACTIVITY_WINDOW_SEC,
                "segment_count": 0,
                "pass1_count": 0,
                "pass2_count": 0,
            },
        }

    pass1 = await _classify_activity_pass1(course_name=course_name, windows=windows)
    pass1 = _smooth_activity_labels(pass1)
    verify_segments = _select_verify_segments(windows, pass1)
    verify_updates = await _verify_activity_pass2(course_name=course_name, verify_segments=verify_segments)

    final_items: List[Dict[str, Any]] = []
    for item in pass1:
        sid = item["segment_id"]
        upd = verify_updates.get(sid)
        if upd:
            if upd.get("keep_current_label", True):
                final_items.append(item)
            else:
                final_items.append(
                    {
                        **item,
                        "activity_type": _normalize_activity_type(upd.get("activity_type", item["activity_type"])),
                        "confidence": round(max(item.get("confidence", 0.0), _safe_float(upd.get("confidence"), 0.0)), 4),
                        "evidence_text": _normalize_text(upd.get("evidence_text", item.get("evidence_text", "")))[:40],
                    }
                )
        else:
            final_items.append(item)

    timeline = _merge_activity_timeline(windows, final_items, duration_sec)
    distribution = _build_activity_distribution(timeline, duration_sec)
    quality_checks = _build_activity_quality_checks(timeline, duration_sec)
    return {
        "timeline": [
            {
                "start_sec": item["start_sec"],
                "end_sec": item["end_sec"],
                "duration_sec": int(item["end_sec"] - item["start_sec"]),
                "activity_type": item["activity_type"],
                "activity_label": _ACTIVITY_LABELS_ZH[_normalize_activity_type(item["activity_type"])],
                "confidence": item["confidence"],
                "evidence_text": item.get("evidence_text", ""),
            }
            for item in timeline
        ],
        "distribution": distribution,
        "quality_checks": quality_checks,
        "meta": {
            "window_size_sec": int(windows[0]["end_sec"] - windows[0]["start_sec"]) if windows else _ACTIVITY_WINDOW_SEC,
            "segment_count": len(windows),
            "pass1_count": len(pass1),
            "pass2_count": len(verify_segments),
        },
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
    if (_safe_float(request.teacher_weight, 0.0) + _safe_float(request.ocr_weight, 0.0)) <= 0:
        raise QualityServiceError(400, 40001, "teacher_weight + ocr_weight 必须大于0")

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
        "teacher_weight": request.teacher_weight,
        "ocr_weight": request.ocr_weight,
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


async def run_lesson_analysis_background(
    course_id: str,
    lesson_id: str,
    teacher_weight: float = 0.6,
    ocr_weight: float = 0.4,
) -> None:
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
            asr_segments = asr_payload.asr_json if asr_payload and isinstance(asr_payload.asr_json, list) else []
            stats = _extract_asr_stats(asr_segments)
            avg_head = _safe_float(lesson.avg_head_up_rate, 0.0)

            ocr_rows = (
                await db.execute(select(OcrSegment).where(OcrSegment.lesson_ref_id == lesson.id))
            ).scalars().all()
            ocr_segments = [
                {
                    "time_offset": int(row.time_offset),
                    "page_num": int(row.page_num),
                    "ocr_content": row.ocr_content,
                    "ocr_keywords": row.ocr_keywords if isinstance(row.ocr_keywords, list) else [],
                }
                for row in ocr_rows
            ]

            course = await db.scalar(select(Course).where(Course.id == course_id))
            course_name = course.course_name if course else "未知课程"

            bloom_payload = await _build_bloom_payload(
                course_name=course_name,
                asr_segments=asr_segments,
                ocr_segments=ocr_segments,
                teacher_weight=teacher_weight,
                ocr_weight=ocr_weight,
            )
            activity_mix_payload = await _build_activity_mix_payload(
                course_name=course_name,
                asr_segments=asr_segments,
                ocr_segments=ocr_segments,
            )
            bands = bloom_payload.get("bands", {})
            bloom_high = int(bands.get("high", 0))

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
                module_name="teaching_activity_mix",
                payload=activity_mix_payload,
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
