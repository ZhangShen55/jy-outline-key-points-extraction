"""
响应模型
"""
from typing import Any, Generic, Optional, TypeVar, Dict, List
from pydantic import BaseModel, Field
from datetime import datetime

T = TypeVar("T")


class ResponseModel(BaseModel, Generic[T]):
    """统一响应模型"""

    code: int = Field(200, description="状态码")
    message: str = Field("success", description="响应消息")
    data: Optional[T] = None


class TaskResponse(BaseModel):
    """任务响应"""

    task_id: str = Field(..., description="任务ID")
    status: str = Field(..., description="任务状态: pending/processing/completed/failed")
    message: str = Field(..., description="状态消息")


class TaskStatusResponse(BaseModel):
    """任务状态响应"""

    task_id: str
    status: str
    message: str
    filename: str
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    failed_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class TaskListResponse(BaseModel):
    """任务列表响应"""

    stats: Dict[str, int] = Field(..., description="任务统计")
    tasks: List[Dict[str, Any]] = Field(..., description="任务列表")


class HealthResponse(BaseModel):
    """健康检查响应"""

    status: str = Field("healthy", description="服务状态")
    service: str = Field(..., description="服务名称")
    version: str = Field(..., description="版本号")
    tasks_count: int = Field(0, description="任务数量")


# 快捷响应构造
def success(data: Any = None, message: str = "success") -> dict:
    """成功响应"""
    return {"code": 200, "message": message, "data": data}


def error(code: int = 400, message: str = "error") -> dict:
    """错误响应"""
    return {"code": code, "message": message, "data": None}
