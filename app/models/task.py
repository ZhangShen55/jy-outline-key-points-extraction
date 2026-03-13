"""
任务模型
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, SmallInteger, Float, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(50), unique=True, nullable=False, index=True)
    task_type = Column(String(20), nullable=False, index=True)
    status = Column(SmallInteger, nullable=False, default=1, index=True)
    stage = Column(String(50))
    progress = Column(SmallInteger, default=0)

    filename = Column(String(255))
    file_size = Column(Integer)

    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    started_at = Column(TIMESTAMP)
    completed_at = Column(TIMESTAMP)
    failed_at = Column(TIMESTAMP)
    elapsed_time = Column(Float)

    error = Column(Text)
    result = Column(JSONB)
    metadata = Column(JSONB)

    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)
