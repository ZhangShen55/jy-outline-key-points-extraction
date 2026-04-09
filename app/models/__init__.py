"""
数据库模型
"""
from app.models.task import Task
from app.models.syllabus import Syllabus, Chapter, KnowledgePoint, Lexicon
from app.models.quality import (
    Course,
    Lesson,
    LessonAsrPayload,
    OcrSegment,
    QualityTaxonomyTerm,
    AnalysisTask,
    AnalysisTaskEvent,
    AiAnalysisReport,
)

__all__ = [
    "Task",
    "Syllabus",
    "Chapter",
    "KnowledgePoint",
    "Lexicon",
    "Course",
    "Lesson",
    "LessonAsrPayload",
    "OcrSegment",
    "QualityTaxonomyTerm",
    "AnalysisTask",
    "AnalysisTaskEvent",
    "AiAnalysisReport",
]
