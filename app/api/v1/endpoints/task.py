"""任务管理端点。"""
from fastapi import APIRouter
from typing import Dict

from app.schemas.response import TaskListResponse
from app.core.logging_config import get_logger
from app.core.exceptions import NotFoundException

logger = get_logger(__name__)
router = APIRouter()

# 共享文档任务状态存储
from app.api.v1.endpoints.document import tasks


@router.get("/list", response_model=TaskListResponse)
async def list_tasks():
    """列出任务列表。"""
    task_list = [
        {
            "task_id": tid,
            "status": t["status"],
            "filename": t["filename"],
            "message": t["message"],
            "created_at": t["created_at"]
        }
        for tid, t in tasks.items()
    ]

    # 汇总各状态的任务数量
    stats = {
        "total": len(task_list),
        "pending": sum(1 for t in task_list if t["status"] == "pending"),
        "processing": sum(1 for t in task_list if t["status"] == "processing"),
        "completed": sum(1 for t in task_list if t["status"] == "completed"),
        "failed": sum(1 for t in task_list if t["status"] == "failed")
    }

    return TaskListResponse(stats=stats, tasks=task_list)


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    """删除任务。"""
    if task_id not in tasks:
        raise NotFoundException(message=f"任务 {task_id} 不存在")

    del tasks[task_id]
    logger.info(f"🗑️ 删除任务 {task_id}")

    return {"message": f"任务 {task_id} 已删除"}
