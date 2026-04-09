"""质量画像模块数据库模型。"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BIGINT,
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class Course(Base):
    __tablename__ = "courses"

    id = Column(UUID(as_uuid=False), primary_key=True)
    syllabus_id = Column(UUID(as_uuid=False))
    course_name = Column(String(255), nullable=False)
    academic_year = Column(String(32))
    teacher = Column(String(255))
    total_weeks = Column(Integer, nullable=False, default=16)
    total_lessons = Column(Integer, nullable=False, default=32)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("total_weeks > 0", name="ck_courses_total_weeks_gt_zero"),
        CheckConstraint("total_lessons > 0", name="ck_courses_total_lessons_gt_zero"),
    )


class Lesson(Base):
    __tablename__ = "lessons"

    id = Column(UUID(as_uuid=False), primary_key=True)
    course_id = Column(UUID(as_uuid=False), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_id = Column(String(80), nullable=False)
    week_number = Column(Integer, nullable=False)
    lesson_index_in_week = Column(Integer, nullable=False)
    lesson_index_global = Column(Integer, nullable=False)
    start_time = Column(TIMESTAMP)
    end_time = Column(TIMESTAMP)
    avg_head_up_rate = Column(Numeric(5, 4))
    status = Column(SmallInteger, nullable=False, default=0, index=True)
    failed_reason = Column(Text)
    analysis_updated_at = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("course_id", "lesson_id", name="uq_lessons_course_lesson"),
        UniqueConstraint("course_id", "week_number", "lesson_index_in_week", name="uq_lessons_course_week_index"),
        UniqueConstraint("course_id", "lesson_index_global", name="uq_lessons_course_global_index"),
        CheckConstraint("week_number > 0", name="ck_lessons_week_gt_zero"),
        CheckConstraint("lesson_index_in_week > 0", name="ck_lessons_week_index_gt_zero"),
        CheckConstraint("lesson_index_global > 0", name="ck_lessons_global_index_gt_zero"),
        CheckConstraint(
            "avg_head_up_rate IS NULL OR (avg_head_up_rate >= 0 AND avg_head_up_rate <= 1)",
            name="ck_lessons_head_up_rate_range",
        ),
        CheckConstraint("status IN (0,1,2,3,4)", name="ck_lessons_status_enum"),
    )


class LessonAsrPayload(Base):
    __tablename__ = "lesson_asr_payloads"

    lesson_ref_id = Column(
        UUID(as_uuid=False),
        ForeignKey("lessons.id", ondelete="CASCADE"),
        primary_key=True,
    )
    asr_json = Column(JSONB, nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)


class OcrSegment(Base):
    __tablename__ = "ocr_segments"

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    lesson_ref_id = Column(UUID(as_uuid=False), ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    time_offset = Column(Integer, nullable=False)
    page_num = Column(Integer, nullable=False)
    ocr_content = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("time_offset >= 0", name="ck_ocr_time_offset_gte_zero"),
        CheckConstraint("page_num > 0", name="ck_ocr_page_num_gt_zero"),
    )


class QualityTaxonomyTerm(Base):
    __tablename__ = "quality_taxonomy_terms"

    id = Column(UUID(as_uuid=False), primary_key=True)
    course_id = Column(UUID(as_uuid=False), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    term_type = Column(String(16), nullable=False)
    category_name = Column(String(128), nullable=False)
    keyword = Column(String(128), nullable=False)
    embedding = Column(Vector(1024))
    confidence = Column(Numeric(5, 4), nullable=False, default=0)
    evidence_lessons = Column(Integer, nullable=False, default=0)
    evidence_weeks = Column(Integer, nullable=False, default=0)
    first_seen_week = Column(Integer)
    last_seen_week = Column(Integer)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("course_id", "term_type", "category_name", "keyword", name="uq_quality_terms_unique"),
        CheckConstraint("term_type IN ('ideology', 'innovation')", name="ck_quality_terms_type_enum"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_quality_terms_confidence_range"),
        CheckConstraint("evidence_lessons >= 0", name="ck_quality_terms_evidence_lessons_gte_zero"),
        CheckConstraint("evidence_weeks >= 0", name="ck_quality_terms_evidence_weeks_gte_zero"),
        CheckConstraint("first_seen_week IS NULL OR first_seen_week > 0", name="ck_quality_terms_first_week_gt_zero"),
        CheckConstraint("last_seen_week IS NULL OR last_seen_week > 0", name="ck_quality_terms_last_week_gt_zero"),
    )


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"

    id = Column(UUID(as_uuid=False), primary_key=True)
    course_id = Column(UUID(as_uuid=False), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    task_kind = Column(String(20), nullable=False)
    target_week = Column(Integer)
    status = Column(SmallInteger, nullable=False, default=0, index=True)
    force_run = Column(Boolean, nullable=False, default=False)
    dedupe_key = Column(String(200), nullable=False, index=True)
    requeue_needed = Column(Boolean, nullable=False, default=False)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    current_node = Column(String(128))
    graph_state = Column(JSONB)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    failed_reason = Column(Text)
    started_at = Column(TIMESTAMP)
    finished_at = Column(TIMESTAMP)
    cancelled_at = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("task_kind IN ('week_profile', 'semester_profile')", name="ck_analysis_tasks_kind_enum"),
        CheckConstraint("target_week IS NULL OR target_week > 0", name="ck_analysis_tasks_target_week_gt_zero"),
        CheckConstraint("status IN (0,1,2,3,4)", name="ck_analysis_tasks_status_enum"),
        CheckConstraint("attempts >= 0", name="ck_analysis_tasks_attempts_gte_zero"),
        CheckConstraint("max_attempts > 0", name="ck_analysis_tasks_max_attempts_gt_zero"),
    )


class AnalysisTaskEvent(Base):
    __tablename__ = "analysis_task_events"

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    task_id = Column(UUID(as_uuid=False), ForeignKey("analysis_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    level = Column(String(16), nullable=False)
    node_name = Column(String(128))
    event_type = Column(String(64))
    detail = Column(JSONB)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("level IN ('info', 'warn', 'error')", name="ck_analysis_task_events_level_enum"),
    )


class AiAnalysisReport(Base):
    __tablename__ = "ai_analysis_reports"

    id = Column(UUID(as_uuid=False), primary_key=True)
    course_id = Column(UUID(as_uuid=False), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    report_level = Column(String(16), nullable=False)
    target_id = Column(String(80), nullable=False)
    module_name = Column(String(64), nullable=False, index=True)
    report_data = Column(JSONB, nullable=False)
    source_task_id = Column(UUID(as_uuid=False), ForeignKey("analysis_tasks.id", ondelete="SET NULL"))
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("course_id", "report_level", "target_id", "module_name", name="uq_ai_reports_unique"),
        CheckConstraint("report_level IN ('lesson', 'week', 'semester')", name="ck_ai_reports_level_enum"),
    )

