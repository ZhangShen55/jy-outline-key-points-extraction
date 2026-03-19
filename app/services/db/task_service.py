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
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> Task:
        """创建任务"""
        task = Task(
            task_id=task_id,
            task_type=task_type,
            status=TaskStatus.PENDING,
            filename=filename,
            file_size=file_size,
            extra_data=extra_data,
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
        error: Optional[str] = None,
    ) -> None:
        """更新任务状态"""
        update_data: Dict[str, Any] = {"status": status, "updated_at": datetime.utcnow()}

        if error is not None:
            update_data["error"] = error

        # 根据状态设置时间戳
        if status == TaskStatus.PROCESSING:
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
    async def get_queue_stats(db: AsyncSession, task_type: Optional[str] = None) -> Dict[str, Any]:
        """
        获取队列统计信息

        Args:
            task_type: 可选，筛选任务类型（syllabus/lesson）

        Returns:
            {
                "queued": {"total": 2, "list": ["task-1", "task-2"]},
                "processing": {"total": 1, "list": ["task-3"]}
            }
        """
        # 构建查询条件
        conditions = [Task.status.in_([TaskStatus.QUEUED, TaskStatus.PROCESSING])]
        if task_type:
            conditions.append(Task.task_type == task_type)

        # 查询排队中的任务（按创建时间排序）
        queued_result = await db.execute(
            select(Task.task_id)
            .where(Task.status == TaskStatus.QUEUED)
            .where(Task.task_type == task_type if task_type else True)
            .order_by(Task.created_at.asc())
        )
        queued_list = [row[0] for row in queued_result.all()]

        # 查询处理中的任务（按开始时间排序）
        processing_result = await db.execute(
            select(Task.task_id)
            .where(Task.status == TaskStatus.PROCESSING)
            .where(Task.task_type == task_type if task_type else True)
            .order_by(Task.started_at.asc())
        )
        processing_list = [row[0] for row in processing_result.all()]

        return {
            "queued": {"total": len(queued_list), "list": queued_list},
            "processing": {"total": len(processing_list), "list": processing_list},
        }

    @staticmethod
    async def get_task_type_stats(db: AsyncSession, task_type: str) -> Dict[str, Any]:
        """
        获取指定任务类型的统计信息

        Args:
            task_type: 任务类型（syllabus/lesson）

        Returns:
            {
                "total": 100,
                "completed": 95,
                "failed": 3,
                "queued": {...},
                "processing": {...}
            }
        """
        # 统计各状态数量
        result = await db.execute(
            select(Task.status, func.count(Task.id))
            .where(Task.task_type == task_type)
            .group_by(Task.status)
        )
        stats = dict(result.all())

        # 获取队列详情
        queue_stats = await TaskService.get_queue_stats(db, task_type)

        return {
            "total": sum(stats.values()),
            "completed": stats.get(TaskStatus.COMPLETED, 0),
            "failed": stats.get(TaskStatus.FAILED, 0),
            "queued": queue_stats["queued"],
            "processing": queue_stats["processing"],
        }
