"""
大纲相关模型
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, TIMESTAMP, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from app.core.database import Base


class Syllabus(Base):
    __tablename__ = "syllabuses"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(50), unique=True, nullable=False, index=True)
    course = Column(String(255), nullable=False, index=True)
    filename = Column(String(255))
    raw_result = Column(JSONB, nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    chapters = relationship("Chapter", back_populates="syllabus", cascade="all, delete-orphan")


class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True, index=True)
    syllabus_id = Column(Integer, ForeignKey("syllabuses.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_num = Column(Integer)
    chapter_title = Column(String(500), nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    syllabus = relationship("Syllabus", back_populates="chapters")
    knowledge_points = relationship("KnowledgePoint", back_populates="chapter", cascade="all, delete-orphan")


class KnowledgePoint(Base):
    __tablename__ = "knowledge_points"

    id = Column(Integer, primary_key=True, index=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(20), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    summary = Column(Text)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    chapter = relationship("Chapter", back_populates="knowledge_points")
    lexicons = relationship("Lexicon", back_populates="knowledge_point", cascade="all, delete-orphan")


class Lexicon(Base):
    __tablename__ = "lexicons"

    id = Column(Integer, primary_key=True, index=True)
    knowledge_point_id = Column(Integer, ForeignKey("knowledge_points.id", ondelete="CASCADE"), nullable=False, index=True)
    term = Column(String(200), nullable=False)
    embedding = Column(Vector(384))
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    knowledge_point = relationship("KnowledgePoint", back_populates="lexicons")
