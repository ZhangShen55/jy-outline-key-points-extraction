"""
课堂分析端点
"""
import asyncio
import uuid
from datetime import datetime
from typing import Dict
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends

from app.api.v1.endpoints.document import tasks
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.database import get_db
from app.core.constants import TaskStatus, TaskType
from app.core.exceptions import NotFoundException
from app.schemas.request import LessonAnalyzeRequest
from app.schemas.response import TaskResponse, TaskStatusResponse
from app.services.db.task_service import TaskService

logger = get_logger(__name__)
router = APIRouter()

_settings = get_settings()
_semaphore = asyncio.Semaphore(_settings.MAX_CONCURRENT)


async def _run_lesson_background(
    task_id: str, syllabus_result: dict, text_segments: list, db: AsyncSession
):
    start_time = datetime.utcnow()
    try:
        async with _semaphore:
            await TaskService.update_task_status(db, task_id, TaskStatus.PROCESSING)
            tasks[task_id]["status"] = TaskStatus.PROCESSING
            tasks[task_id]["message"] = "处理中..."
            tasks[task_id]["started_at"] = datetime.utcnow().isoformat()
            logger.info(f"🔄 开始处理课堂分析任务 {task_id}")

            from app.services.lesson_pipeline import run_lesson_pipeline

            result = await run_lesson_pipeline(syllabus_result, text_segments)

            elapsed = (datetime.utcnow() - start_time).total_seconds()
            await TaskService.complete_task(db, task_id, result, elapsed)

            tasks[task_id]["status"] = TaskStatus.COMPLETED
            tasks[task_id]["result"] = result
            tasks[task_id]["completed_at"] = datetime.utcnow().isoformat()
            logger.info(f"✅ 课堂分析任务 {task_id} 完成")

    except Exception as e:
        logger.error(f"❌ 课堂分析任务 {task_id} 失败: {e}")
        await TaskService.fail_task(db, task_id, str(e))
        tasks[task_id]["status"] = TaskStatus.FAILED
        tasks[task_id]["error"] = str(e)


@router.post("/analyze", response_model=TaskResponse, status_code=202)
async def analyze_lesson(
    request: LessonAnalyzeRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    提交课堂语音转写分析任务（异步）

    立即返回任务ID，使用 GET /api/v1/lesson/status/{task_id} 查询进度。
    """
    try:
        # 1. 从数据库查询大纲结果
        from app.services.db.syllabus_service import SyllabusService

        syllabus = await SyllabusService.get_syllabus_by_task_id(db, request.syllabus_id)
        if not syllabus:
            raise HTTPException(
                status_code=404,
                detail=f"大纲 {request.syllabus_id} 不存在，请先使用 /process 接口提取大纲"
            )

        # 2. 使用数据库中的大纲结果
        syllabus_result = syllabus.raw_result

        task_id = f"lesson-{uuid.uuid4().hex[:24]}"

        # 创建数据库任务记录
        await TaskService.create_task(
            db, task_id=task_id, task_type=TaskType.LESSON,
            extra_data={"syllabus_id": request.syllabus_id},
        )

        # 初始化内存任务
        tasks[task_id] = {
            "task_id": task_id,
            "syllabus_id": request.syllabus_id,
            "status": TaskStatus.PENDING,
            "message": "任务已提交，等待处理...",
            "created_at": datetime.utcnow().isoformat(),
        }

        background_tasks.add_task(
            _run_lesson_background,
            task_id,
            syllabus_result,
            request.text_segments,
            db,
        )

        logger.info(f"📝 课堂分析任务 {task_id} 已提交，关联大纲: {request.syllabus_id}")

        return TaskResponse(
            task_id=task_id,
            status="pending",
            message="任务已提交，请使用 GET /api/v1/lesson/status/{task_id} 查询处理进度",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 提交课堂分析任务失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{task_id}", response_model=TaskStatusResponse)
async def get_lesson_status(task_id: str, db: AsyncSession = Depends(get_db)):
    """
    查询课堂分析任务状态

    只查询 /analyze 接口提交的任务，队列信息也只显示课堂分析任务
    """
    # 1. 先查数据库
    db_task = await TaskService.get_task_by_id(db, task_id)
    if db_task:
        # 验证任务类型
        if db_task.task_type != TaskType.LESSON:
            raise NotFoundException(message=f"任务 {task_id} 不是课堂分析任务")

        # 获取课堂分析任务的队列统计
        queue_stats = await TaskService.get_queue_stats(db, TaskType.LESSON)

        return TaskStatusResponse(
            task_id=db_task.task_id,
            status=db_task.status,
            message=_build_message(db_task.status),
            syllabus_id=(db_task.extra_data or {}).get("syllabus_id"),
            created_at=db_task.created_at.isoformat(),
            started_at=db_task.started_at.isoformat() if db_task.started_at else None,
            completed_at=db_task.completed_at.isoformat() if db_task.completed_at else None,
            elapsed_time=db_task.elapsed_time,
            queued=queue_stats["queued"],
            processing=queue_stats["processing"],
            filename=db_task.filename,
            result=db_task.result,
            error=db_task.error,
        )

    # 2. 数据库没有，查内存
    if task_id in tasks:
        task = tasks[task_id]

        # 验证任务类型（通过task_id前缀判断）
        if not task_id.startswith("chatcmpl-"):
            raise NotFoundException(message=f"任务 {task_id} 不是课堂分析任务")

        queue_stats = await TaskService.get_queue_stats(db, TaskType.LESSON)

        return TaskStatusResponse(
            task_id=task_id,
            status=task.get("status", TaskStatus.PENDING),
            message=task.get("message", ""),
            syllabus_id=task.get("syllabus_id"),
            created_at=task.get("created_at", ""),
            started_at=task.get("started_at"),
            completed_at=task.get("completed_at"),
            elapsed_time=None,
            queued=queue_stats["queued"],
            processing=queue_stats["processing"],
            filename=task.get("filename"),
            result=task.get("result"),
            error=task.get("error"),
        )

    raise NotFoundException(message=f"任务 {task_id} 不存在")


def _build_message(status: int) -> str:
    """根据状态码构建消息"""
    if status == TaskStatus.COMPLETED:
        return "处理完成"
    if status == TaskStatus.FAILED:
        return "处理失败"
    if status == TaskStatus.QUEUED:
        return "排队等待中..."
    if status == TaskStatus.PROCESSING:
        return "处理中..."
    return "任务已提交"
