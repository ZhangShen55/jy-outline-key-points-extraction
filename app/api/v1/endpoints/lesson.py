"""
课堂分析端点
"""
import asyncio
import uuid
from datetime import datetime
from typing import Dict

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.api.v1.endpoints.document import tasks
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.schemas.request import LessonAnalyzeRequest
from app.schemas.response import TaskResponse, TaskStatusResponse

logger = get_logger(__name__)
router = APIRouter()

_settings = get_settings()
_semaphore = asyncio.Semaphore(_settings.MAX_CONCURRENT)


async def _run_lesson_background(task_id: str, syllabus_result: dict, text_segments: list):
    try:
        async with _semaphore:
            tasks[task_id]["status"] = "processing"
            tasks[task_id]["message"] = "课堂分析中..."
            tasks[task_id]["started_at"] = datetime.now().isoformat()
            logger.info(f"🔄 开始处理课堂分析任务 {task_id}")

            from app.services.lesson_pipeline import run_lesson_pipeline
            result = await run_lesson_pipeline(syllabus_result, text_segments)

            tasks[task_id]["status"] = "completed"
            tasks[task_id]["result"] = result
            tasks[task_id]["message"] = "处理完成"
            tasks[task_id]["completed_at"] = datetime.now().isoformat()
            logger.info(f"✅ 课堂分析任务 {task_id} 完成")

    except Exception as e:
        logger.error(f"❌ 课堂分析任务 {task_id} 失败: {e}")
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)
        tasks[task_id]["message"] = f"处理失败: {e}"
        tasks[task_id]["failed_at"] = datetime.now().isoformat()


@router.post("/analyze", response_model=TaskResponse, status_code=202)
async def analyze_lesson(request: LessonAnalyzeRequest, background_tasks: BackgroundTasks):
    """
    提交课堂语音转写分析任务（异步）

    立即返回任务ID，使用 GET /api/v1/document/status/{task_id} 查询进度。
    """
    try:
        task_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

        tasks[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "message": "任务已提交，等待处理...",
            "result": None,
            "error": None,
            "created_at": datetime.now().isoformat(),
        }

        background_tasks.add_task(
            _run_lesson_background,
            task_id,
            request.syllabus_result,
            request.text_segments,
        )

        logger.info(f"📝 课堂分析任务 {task_id} 已提交")

        return TaskResponse(
            task_id=task_id,
            status="pending",
            message="任务已提交，请使用 GET /api/v1/document/status/{task_id} 查询处理进度",
        )

    except Exception as e:
        logger.error(f"❌ 提交课堂分析任务失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
