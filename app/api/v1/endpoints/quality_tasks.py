"""质量画像：任务端点。"""

import uuid
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.quality import AnalysisTask, Course
from app.schemas.quality import (
    QualityBaseResponse,
    QualityTaskCancelRequest,
    SemesterProfileGenerateRequest,
    SemesterProfileStatusQueryRequest,
)
from app.services.quality_service import (
    QualityServiceError,
    create_or_mark_semester_task,
    resolve_target_week,
    run_semester_profile_task_background,
    status_name,
)

router = APIRouter()


@router.post("/tasks/semester-profile/generate", response_model=QualityBaseResponse, status_code=202)
async def generate_semester_profile(
    request: SemesterProfileGenerateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """触发学期画像任务。"""
    trace_id = uuid.uuid4().hex
    try:
        course = await db.scalar(select(Course).where(Course.id == request.course_id))
        if course is None:
            return JSONResponse(
                status_code=404,
                content=_resp_body(
                    40401,
                    f"course_id not found: {request.course_id}",
                    None,
                    trace_id,
                ),
            )

        resolved_week, week_source = await resolve_target_week(db, request.course_id, request.target_week)
        task, dedupe_hit = await create_or_mark_semester_task(
            db,
            course_id=request.course_id,
            target_week=resolved_week,
            force_run=bool(request.force),
            target_week_source=week_source,
        )
        if not dedupe_hit:
            background_tasks.add_task(run_semester_profile_task_background, task.id)

        data = {
            "task_id": task.id,
            "course_id": task.course_id,
            "target_week": resolved_week,
            "target_week_source": week_source,
            "status": task.status,
            "status_name": status_name(task.status),
            "dedupe_hit": dedupe_hit,
            "requeue_needed": bool(task.requeue_needed),
            "force_run": bool(task.force_run),
        }
        return QualityBaseResponse(
            code=20000,
            message=(
                "Task accepted for "
                f"course_id={request.course_id}, target_week={resolved_week}, dedupe_hit={str(dedupe_hit).lower()}"
            ),
            data=data,
            trace_id=trace_id,
        )
    except QualityServiceError as e:
        return JSONResponse(status_code=e.http_status, content=_resp_body(e.code, e.message, e.data, trace_id))
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=_resp_body(
                50001,
                f"Internal error on generate: course_id={request.course_id}, err={e}",
                None,
                trace_id,
            ),
        )


@router.post("/tasks/semester-profile/status/query", response_model=QualityBaseResponse)
async def query_semester_profile_status(
    request: SemesterProfileStatusQueryRequest,
    db: AsyncSession = Depends(get_db),
):
    """查询任务状态。"""
    trace_id = uuid.uuid4().hex
    try:
        task = await db.scalar(select(AnalysisTask).where(AnalysisTask.id == request.task_id))
        if task is None:
            return JSONResponse(
                status_code=404,
                content=_resp_body(
                    40403,
                    f"task_id not found: {request.task_id}",
                    None,
                    trace_id,
                ),
            )

        graph_state = task.graph_state or {}
        progress_pct = int(graph_state.get("progress_pct", 0) or 0)
        data = {
            "task_id": task.id,
            "course_id": task.course_id,
            "task_kind": task.task_kind,
            "target_week": task.target_week,
            "target_week_source": graph_state.get("target_week_source"),
            "status": task.status,
            "status_name": status_name(task.status),
            "current_node": task.current_node,
            "progress_pct": max(0, min(100, progress_pct)),
            "cancel_requested": bool(task.cancel_requested),
            "requeue_needed": bool(task.requeue_needed),
            "force_run": bool(task.force_run),
            "attempts": int(task.attempts or 0),
            "max_attempts": int(task.max_attempts or 0),
            "failed_reason": task.failed_reason,
            "created_at": _fmt_dt(task.created_at),
            "started_at": _fmt_dt(task.started_at),
            "finished_at": _fmt_dt(task.finished_at),
            "cancelled_at": _fmt_dt(task.cancelled_at),
            "updated_at": _fmt_dt(task.updated_at),
        }
        return QualityBaseResponse(
            code=20000,
            message=f"Status retrieved successfully for task_id={request.task_id}",
            data=data,
            trace_id=trace_id,
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=_resp_body(
                50001,
                f"Internal error on status query: task_id={request.task_id}, err={e}",
                None,
                trace_id,
            ),
        )


@router.post("/tasks/cancel", response_model=QualityBaseResponse)
async def cancel_quality_task(
    request: QualityTaskCancelRequest,
    db: AsyncSession = Depends(get_db),
):
    """取消任务。"""
    trace_id = uuid.uuid4().hex
    try:
        task = await db.scalar(select(AnalysisTask).where(AnalysisTask.id == request.task_id))
        if task is None:
            return JSONResponse(
                status_code=404,
                content=_resp_body(
                    40403,
                    f"task_id not found: {request.task_id}",
                    None,
                    trace_id,
                ),
            )

        if task.status in (2, 3, 4):
            data = {
                "task_id": task.id,
                "status": task.status,
                "status_name": status_name(task.status),
                "cancel_requested": bool(task.cancel_requested),
            }
            return QualityBaseResponse(
                code=20011,
                message=f"No-op: task already in terminal state ({status_name(task.status)})",
                data=data,
                trace_id=trace_id,
            )

        if bool(task.cancel_requested):
            data = {
                "task_id": task.id,
                "status": task.status,
                "status_name": status_name(task.status),
                "cancel_requested": True,
            }
            return QualityBaseResponse(
                code=20011,
                message="No-op: cancel already requested",
                data=data,
                trace_id=trace_id,
            )

        if task.status == 0:
            task.status = 4
            task.current_node = "cancelled"
            task.cancel_requested = True
            task.cancelled_at = datetime.utcnow()
            task.finished_at = datetime.utcnow()
            task.updated_at = datetime.utcnow()
        else:
            task.cancel_requested = True
            task.updated_at = datetime.utcnow()

        await db.commit()

        data = {
            "task_id": task.id,
            "status": task.status,
            "status_name": status_name(task.status),
            "cancel_requested": bool(task.cancel_requested),
        }
        return QualityBaseResponse(
            code=20000,
            message=f"Cancel request accepted for task_id={task.id}",
            data=data,
            trace_id=trace_id,
        )
    except Exception as e:
        await db.rollback()
        return JSONResponse(
            status_code=500,
            content=_resp_body(
                50001,
                f"Internal error on cancel: task_id={request.task_id}, err={e}",
                None,
                trace_id,
            ),
        )


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
