"""
系统状态端点
"""
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.constants import TaskType
from app.schemas.response import SystemStatusResponse, TaskTypeStats
from app.services.db.task_service import TaskService

router = APIRouter()

# 系统启动时间
_start_time = datetime.utcnow()


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status(db: AsyncSession = Depends(get_db)):
    """
    获取系统整体状态

    包括：
    - 系统运行信息（启动时间、运行时长）
    - 大纲提取任务统计
    - 课堂分析任务统计
    """
    # 系统信息
    uptime_seconds = (datetime.utcnow() - _start_time).total_seconds()
    days = int(uptime_seconds // 86400)
    hours = int((uptime_seconds % 86400) // 3600)
    minutes = int((uptime_seconds % 3600) // 60)
    seconds = int(uptime_seconds % 60)
    uptime_str = f"{days}天{hours}小时{minutes}分{seconds}秒"

    system_info = {
        "start_time": _start_time.isoformat(),
        "uptime": uptime_str,
        "version": "1.0.0",
    }

    # 大纲提取统计
    syllabus_stats = await TaskService.get_task_type_stats(db, TaskType.SYLLABUS)

    # 课堂分析统计
    lesson_stats = await TaskService.get_task_type_stats(db, TaskType.LESSON)

    return SystemStatusResponse(
        system=system_info,
        syllabus=TaskTypeStats(**syllabus_stats),
        lesson=TaskTypeStats(**lesson_stats),
    )
