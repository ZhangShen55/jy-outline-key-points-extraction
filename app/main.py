"""
FastAPI 应用主入口
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging_config import setup_logging, get_logger
from app.api.v1.router import api_v1_router
from app.schemas.response import HealthResponse

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化
    setup_logging()
    logger.info("🚀 应用启动中...")
    yield
    # 关闭时清理
    logger.info("👋 应用关闭中...")


# 应用实例
settings = get_settings()
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="教学大纲四要点核心内容提取系统 - 自动化处理和分析教学大纲文档，提取基本要求、教学重点、教学难点、课程思政四个关键模块",
    lifespan=lifespan,
)

# 挂载 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应收紧
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册全局异常处理
register_exception_handlers(app)

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# 注册路由
app.include_router(api_v1_router, prefix="/api/v1")


@app.get("/", tags=["根路径"])
async def root():
    """根路径，返回 API 信息"""
    return {
        "message": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs": "/docs",
        "redoc": "/redoc",
        "health": "/health"
    }


@app.get("/health", response_model=HealthResponse, tags=["健康检查"])
async def health_check():
    """健康检查接口"""
    from app.api.v1.endpoints.document import tasks

    return HealthResponse(
        status="healthy",
        service=settings.PROJECT_NAME,
        version=settings.VERSION,
        tasks_count=len(tasks)
    )


if __name__ == "__main__":
    import uvicorn

    logger.info(f"🚀 启动 {settings.PROJECT_NAME}")
    logger.info(f"📍 服务地址: http://0.0.0.0:8000")
    logger.info(f"📝 API文档: http://0.0.0.0:8000/docs")
    logger.info(f"📖 ReDoc文档: http://0.0.0.0:8000/redoc")

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower()
    )


# 运行命令示例: uvicorn app.main:app --reload --port 8000