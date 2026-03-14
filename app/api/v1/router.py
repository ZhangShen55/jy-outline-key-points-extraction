"""
API v1 路由汇总
"""
from fastapi import APIRouter

from app.api.v1.endpoints import document, lesson, task, system, lexicon

api_v1_router = APIRouter()

# 注册各端点路由
api_v1_router.include_router(document.router, prefix="/document", tags=["文档处理"])
api_v1_router.include_router(task.router, prefix="/task", tags=["任务管理"])
api_v1_router.include_router(lesson.router, prefix="/lesson", tags=["课堂分析"])
api_v1_router.include_router(system.router, prefix="/system", tags=["系统状态"])
api_v1_router.include_router(lexicon.router, prefix="/syllabus", tags=["词库管理"])
