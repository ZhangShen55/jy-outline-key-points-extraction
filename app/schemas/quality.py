"""质量画像模块请求/响应模型（骨架版）。"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class QualityBaseResponse(BaseModel):
    """质量画像统一响应。"""

    code: int = Field(..., description="业务状态码")
    message: str = Field(..., description="响应消息")
    data: Optional[Dict[str, Any]] = Field(None, description="响应数据")
    trace_id: str = Field(..., description="链路追踪ID")


class AsrSegment(BaseModel):
    """ASR 切片。"""

    bg: float = Field(..., description="起始时间（秒）")
    ed: float = Field(..., description="结束时间（秒）")
    role: Optional[str] = Field(None, description="说话角色")
    text: str = Field(..., min_length=1, description="转写文本")
    emotion: Optional[str] = Field(None, description="情绪标签（上游可选）")
    speed: Optional[float] = Field(None, ge=0, description="语速（字/分钟，上游可选）")


class OcrSegment(BaseModel):
    """OCR 切片。"""

    time_offset: int = Field(..., ge=0, description="相对课时起点秒级偏移（必填）")
    page_num: int = Field(..., ge=1, description="课件页码（必填）")
    ocr_content: str = Field(..., min_length=1, description="OCR文本")


class QualityDataIngestionRequest(BaseModel):
    """多模态数据接入请求。"""

    course_id: str = Field(..., description="课程ID")
    course_name: str = Field(..., min_length=1, description="课程名称")
    academic_year: Optional[str] = Field(None, description="学年学期（可选）")
    teacher: Optional[str] = Field(None, description="教师（可选）")
    total_weeks: Optional[int] = Field(16, ge=1, description="课程总周数")
    total_lessons: Optional[int] = Field(32, ge=1, description="课程总课时")

    lesson_id: str = Field(..., description="上游课时ID（在course内唯一）")
    week_number: int = Field(..., ge=1, description="第几周（必填）")
    lesson_index_in_week: int = Field(..., ge=1, description="周内课程序号（必填）")
    lesson_index_global: int = Field(..., ge=1, description="学期全局课程序号（必填）")
    avg_head_up_rate: Optional[float] = Field(None, ge=0, le=1, description="平均抬头率，建议0~1")

    asr_data: List[AsrSegment] = Field(default_factory=list, description="ASR切片列表")
    ocr_data: List[OcrSegment] = Field(default_factory=list, description="OCR切片列表")


class SemesterProfileGenerateRequest(BaseModel):
    """触发学期画像任务请求。"""

    course_id: str = Field(..., description="课程ID")
    target_week: Optional[int] = Field(None, ge=1, description="目标周，空表示截至最新周")
    force: bool = Field(False, description="是否强制重算")


class SemesterProfileStatusQueryRequest(BaseModel):
    """查询任务状态请求。"""

    task_id: str = Field(..., description="任务ID")


class SemesterProfileModuleQueryRequest(BaseModel):
    """查询看板模块数据请求。"""

    course_id: str = Field(..., description="课程ID")
    report_level: str = Field(..., description="报告层级：lesson/week/semester")
    target_identifier: str = Field(..., description="层级目标ID")
    module_name: str = Field(..., description="模块名")


class QualityTaskCancelRequest(BaseModel):
    """取消任务请求。"""

    task_id: str = Field(..., description="任务ID")

