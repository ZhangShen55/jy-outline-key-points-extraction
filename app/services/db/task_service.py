"""
任务数据库服务
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.core.constants import TaskStatus


class TaskService:
    """任务 CRUD 服务"""

    @staticmethod
    async def create_task(
        db: AsyncSession,
        task_id: str,
        task_type: str,
        filename: Optional[str] = None,
        file_size: Optional[int] = None,
    ) -> Task:
        """创建任务"""
        task = Task(
            task_id=task_id,
            task_type=task_type,
            status=TaskStatus.PENDING,
            filename=filename,
            file_size=file_size,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def get_task_by_id(db: AsyncSession, task_id: str) -> Optional[Task]:
        """根据 task_id 查询任务"""
        result = await db.execute(select(Task).where(Task.task_id == task_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def update_task_status(
        db: AsyncSession,
        task_id: str,
        status: int,
        stage: Optional[str] = None,
        progress: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        """更新任务状态"""
        update_data: Dict[str, Any] = {"status": status, "updated_at": datetime.utcnow()}

        if stage is not None:
            update_data["stage"] = stage
        if progress is not None:
            update_data["progress"] = progress
        if error is not None:
            update_data["error"] = error

        # 根据状态设置时间戳
        if status == TaskStatus.PROCESSING and "started_at" not in update_data:
            update_data["started_at"] = datetime.utcnow()
        elif status == TaskStatus.COMPLETED:
            update_data["completed_at"] = datetime.utcnow()
        elif status == TaskStatus.FAILED:
            update_data["failed_at"] = datetime.utcnow()

        await db.execute(update(Task).where(Task.task_id == task_id).values(**update_data))
        await db.commit()

    @staticmethod
    async def complete_task(
        db: AsyncSession,
        task_id: str,
        result: Dict[str, Any],
        elapsed_time: float,
    ) -> None:
        """完成任务"""
        await db.execute(
            update(Task)
            .where(Task.task_id == task_id)
            .values(
                status=TaskStatus.COMPLETED,
                result=result,
                elapsed_time=elapsed_time,
                completed_at=datetime.utcnow(),
                progress=100,
                updated_at=datetime.utcnow(),
            )
        )
        await db.commit()

    @staticmethod
    async def fail_task(db: AsyncSession, task_id: str, error: str) -> None:
        """任务失败"""
        await db.execute(
            update(Task)
            .where(Task.task_id == task_id)
            .values(
                status=TaskStatus.FAILED,
                error=error,
                failed_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        await db.commit()

    @staticmethod
    async def get_queue_stats(db: AsyncSession) -> Dict[str, int]:
        """获取队列统计信息"""
        result = await db.execute(
            select(Task.status, func.count(Task.id))
            .where(Task.status.in_([TaskStatus.QUEUED, TaskStatus.PROCESSING]))
            .group_by(Task.status)
        )
        stats = dict(result.all())
        return {
            "queued": stats.get(TaskStatus.QUEUED, 0),
            "processing": stats.get(TaskStatus.PROCESSING, 0),
        }
