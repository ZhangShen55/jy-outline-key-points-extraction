"""质量画像：数据接入端点。"""

import uuid
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_quality_db
from app.schemas.quality import QualityBaseResponse, QualityDataIngestionRequest
from app.services.quality_service import (
    QualityServiceError,
    ingest_data,
    run_lesson_analysis_background,
)

router = APIRouter()


@router.post("/courses/data-ingestion", response_model=QualityBaseResponse, status_code=202)
async def data_ingestion(
    request: QualityDataIngestionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_quality_db),
):
    """接收课时多模态数据并触发分析。"""
    trace_id = uuid.uuid4().hex
    try:
        data = await ingest_data(db, request)
        background_tasks.add_task(run_lesson_analysis_background, request.course_id, request.lesson_id)
        return QualityBaseResponse(
            code=20000,
            message=(
                "Data ingested successfully for "
                f"course_id={request.course_id}, lesson_id={request.lesson_id}"
            ),
            data=data,
            trace_id=trace_id,
        )
    except QualityServiceError as e:
        body = _resp_body(e.code, e.message, e.data, trace_id)
        return JSONResponse(status_code=e.http_status, content=body)
    except Exception as e:
        body = _resp_body(
            50001,
            (
                "Internal error on data-ingestion: "
                f"course_id={request.course_id}, lesson_id={request.lesson_id}, err={e}"
            ),
            None,
            trace_id,
        )
        return JSONResponse(status_code=500, content=body)


def _resp_body(code: int, message: str, data: Dict[str, Any] | None, trace_id: str) -> Dict[str, Any]:
    return QualityBaseResponse(
        code=code,
        message=message,
        data=data,
        trace_id=trace_id,
    ).model_dump()
