"""数据库基础配置。"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

# 主数据库连接 URL（syllabus/task 等）
DATABASE_URL = f"postgresql://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
ASYNC_DATABASE_URL = f"postgresql+asyncpg://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"

# 质量画像数据库连接 URL（quality_*）
QUALITY_DATABASE_URL = (
    f"postgresql://{settings.QUALITY_DB_USER}:{settings.QUALITY_DB_PASSWORD}"
    f"@{settings.QUALITY_DB_HOST}:{settings.QUALITY_DB_PORT}/{settings.QUALITY_DB_NAME}"
)
QUALITY_ASYNC_DATABASE_URL = (
    f"postgresql+asyncpg://{settings.QUALITY_DB_USER}:{settings.QUALITY_DB_PASSWORD}"
    f"@{settings.QUALITY_DB_HOST}:{settings.QUALITY_DB_PORT}/{settings.QUALITY_DB_NAME}"
)

# 同步引擎（用于初始化建表）
engine = create_engine(DATABASE_URL, echo=False)
quality_engine = create_engine(QUALITY_DATABASE_URL, echo=False)

# 异步引擎（主库）
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
)

# 异步引擎（质量画像库）
quality_async_engine = create_async_engine(
    QUALITY_ASYNC_DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=settings.QUALITY_DB_POOL_SIZE,
    max_overflow=settings.QUALITY_DB_MAX_OVERFLOW,
    pool_timeout=settings.QUALITY_DB_POOL_TIMEOUT,
)

# 会话工厂（主库）
AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# 会话工厂（质量画像库）
QualityAsyncSessionLocal = async_sessionmaker(
    quality_async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# 基类（共享 metadata，按 engine + tables 做选择性建表）
Base = declarative_base()


# 依赖注入：获取数据库会话
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_quality_db():
    """依赖注入：获取质量画像数据库会话。"""
    async with QualityAsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
