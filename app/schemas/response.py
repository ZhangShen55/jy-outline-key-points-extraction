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


class QueueInfo(BaseModel):
    """队列信息"""
    total: int
    list: List[str]


class TaskStatusResponse(BaseModel):
    """任务状态响应"""

    task_id: str
    status: int  # 0=completed, 1=pending, 2=queued, 3=processing, 4=failed
    message: str

    # 时间信息
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    elapsed_time: Optional[float] = None

    # 队列信息
    queued: QueueInfo
    processing: QueueInfo

    # 结果和错误
    filename: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class TaskListResponse(BaseModel):
    """任务列表响应"""

    stats: Dict[str, int] = Field(..., description="任务统计")
    tasks: List[Dict[str, Any]] = Field(..., description="任务列表")


class TaskTypeStats(BaseModel):
    """任务类型统计"""
    total: int
    completed: int
    failed: int
    queued: QueueInfo
    processing: QueueInfo


class SystemStatusResponse(BaseModel):
    """系统状态响应"""
    system: Dict[str, Any]
    syllabus: TaskTypeStats
    lesson: TaskTypeStats


class LexiconResponse(BaseModel):
    """词库响应"""
    task_id: str
    chapter_num: int
    chapter_title: str
    point_title: str
    category: str
    lexicons: List[str]


class LexiconMatchSource(BaseModel):
    """匹配结果来源"""
    task_id: str
    chapter_num: int
    chapter_title: str
    category: str
    point_title: str


class LexiconMatchItem(BaseModel):
    """单条匹配结果"""
    course: str
    lexicon: str
    score: float
    source: LexiconMatchSource


class LexiconMatchResponse(BaseModel):
    """词库匹配响应"""
    text: str
    top: int
    search_scope: Dict[str, Any]
    status_code: int
    message: str
    results: List[LexiconMatchItem]


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
