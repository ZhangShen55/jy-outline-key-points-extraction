"""
数据库初始化脚本
"""
import asyncio
import sys
from pathlib import Path

from app.core.database import async_engine, quality_async_engine, Base
from app.core.logging_config import get_logger
from app.models import (
    Task,
    Syllabus,
    Chapter,
    KnowledgePoint,
    Lexicon,
    Course,
    Lesson,
    LessonAsrPayload,
    OcrSegment,
    QualityTaxonomyTerm,
    AnalysisTask,
    AnalysisTaskEvent,
    AiAnalysisReport,
)

logger = get_logger(__name__)


async def drop_all():
    """删除所有表（慎用，按主库/质量库分别删除）。"""
    logger.warning("⚠️ 准备删除主库表...")
    async with async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=_SYLLABUS_TABLES))
    logger.warning("⚠️ 准备删除质量库表...")
    async with quality_async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=_QUALITY_TABLES))
    logger.info("✅ 主库与质量库表已删除")


async def init_db():
    """初始化数据库表（主库/质量库分离）。"""
    logger.info("开始初始化主库表...")
    async with async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=_SYLLABUS_TABLES))
    logger.info("✅ 主库表初始化完成")

    logger.info("开始初始化质量库表...")
    async with quality_async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=_QUALITY_TABLES))
    logger.info("✅ 质量库表初始化完成")


async def main():
    """主函数"""
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--drop":
            await drop_all()
        await init_db()
    finally:
        await async_engine.dispose()
        await quality_async_engine.dispose()


_SYLLABUS_TABLES = [
    Task.__table__,
    Syllabus.__table__,
    Chapter.__table__,
    KnowledgePoint.__table__,
    Lexicon.__table__,
]

_QUALITY_TABLES = [
    Course.__table__,
    Lesson.__table__,
    LessonAsrPayload.__table__,
    OcrSegment.__table__,
    QualityTaxonomyTerm.__table__,
    AnalysisTask.__table__,
    AnalysisTaskEvent.__table__,
    AiAnalysisReport.__table__,
]


if __name__ == "__main__":
    asyncio.run(main())
