"""
数据库模型
"""
from app.models.task import Task
from app.models.syllabus import Syllabus, Chapter, KnowledgePoint, Lexicon

__all__ = ["Task", "Syllabus", "Chapter", "KnowledgePoint", "Lexicon"]
