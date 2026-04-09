"""
数据库初始化脚本
"""
import asyncio
import sys
from pathlib import Path

from app.core.database import async_engine, Base
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
    """删除所有表（慎用）"""
    logger.warning("⚠️ 准备删除所有表...")
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    logger.info("✅ 所有表已删除")


async def init_db():
    """初始化数据库表"""
    logger.info("开始初始化数据库...")
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ 数据库初始化完成")


async def main():
    """主函数"""
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--drop":
            await drop_all()
        await init_db()
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
