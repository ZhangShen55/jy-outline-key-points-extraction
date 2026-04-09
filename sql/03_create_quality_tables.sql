-- ============================================
-- 学期综合质量画像模块：数据库结构
-- 说明：
-- 1) 新增模块，01、02 不做改动
-- 2) 状态码统一用 SMALLINT，便于高效检索
-- 3) 关键字段已补充 COMMENT，避免语义不清
-- ============================================

-- 启用 pgvector（若已启用会自动跳过）
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================
-- 1) 课程主表
-- ============================================
CREATE TABLE IF NOT EXISTS courses (
    id UUID PRIMARY KEY,
    syllabus_id UUID,
    course_name VARCHAR(255) NOT NULL,
    academic_year VARCHAR(32),
    teacher VARCHAR(255), -- 非强制，可为 NULL
    total_weeks INT NOT NULL DEFAULT 16 CHECK (total_weeks > 0),
    total_lessons INT NOT NULL DEFAULT 32 CHECK (total_lessons > 0),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_courses_course_name ON courses(course_name);
CREATE INDEX IF NOT EXISTS idx_courses_academic_year ON courses(academic_year);

COMMENT ON TABLE courses IS '课程主表（上游业务平台传入主数据）';
COMMENT ON COLUMN courses.syllabus_id IS '可选：关联教学大纲ID';
COMMENT ON COLUMN courses.teacher IS '主讲教师，可为空';
COMMENT ON COLUMN courses.total_weeks IS '课程总周数，默认16';
COMMENT ON COLUMN courses.total_lessons IS '课程总课时数，默认32';

-- ============================================
-- 2) 课时表
-- ============================================
CREATE TABLE IF NOT EXISTS lessons (
    id UUID PRIMARY KEY, -- 系统内部课时主键
    course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    lesson_id VARCHAR(80) NOT NULL, -- 上游 lesson_id（仅在 course 内唯一）
    week_number INT NOT NULL CHECK (week_number > 0),
    lesson_index_in_week INT NOT NULL CHECK (lesson_index_in_week > 0),
    lesson_index_global INT NOT NULL CHECK (lesson_index_global > 0),
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    avg_head_up_rate NUMERIC(5,4) CHECK (avg_head_up_rate IS NULL OR (avg_head_up_rate >= 0 AND avg_head_up_rate <= 1)),
    score_high_order NUMERIC(5,2) CHECK (score_high_order IS NULL OR (score_high_order >= 0 AND score_high_order <= 100)),
    score_innovation NUMERIC(5,2) CHECK (score_innovation IS NULL OR (score_innovation >= 0 AND score_innovation <= 100)),
    score_fun_experience NUMERIC(5,2) CHECK (score_fun_experience IS NULL OR (score_fun_experience >= 0 AND score_fun_experience <= 100)),
    score_challenge NUMERIC(5,2) CHECK (score_challenge IS NULL OR (score_challenge >= 0 AND score_challenge <= 100)),
    score_ideology NUMERIC(5,2) CHECK (score_ideology IS NULL OR (score_ideology >= 0 AND score_ideology <= 100)),
    status SMALLINT NOT NULL DEFAULT 0 CHECK (status IN (0, 1, 2, 3, 4)),
    failed_reason TEXT,
    analysis_updated_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(course_id, lesson_id),
    UNIQUE(course_id, week_number, lesson_index_in_week),
    UNIQUE(course_id, lesson_index_global)
);

CREATE INDEX IF NOT EXISTS idx_lessons_course_id ON lessons(course_id);
CREATE INDEX IF NOT EXISTS idx_lessons_status ON lessons(status);
CREATE INDEX IF NOT EXISTS idx_lessons_week ON lessons(course_id, week_number);
CREATE INDEX IF NOT EXISTS idx_lessons_week_index ON lessons(course_id, week_number, lesson_index_in_week);
CREATE INDEX IF NOT EXISTS idx_lessons_global_index ON lessons(course_id, lesson_index_global);
CREATE INDEX IF NOT EXISTS idx_lessons_analysis_updated_at ON lessons(analysis_updated_at);

COMMENT ON TABLE lessons IS '课时表（按课程维度管理上游课时数据和处理状态）';
COMMENT ON COLUMN lessons.lesson_id IS '上游课时ID，仅在同一course_id范围内唯一';
COMMENT ON COLUMN lessons.lesson_index_in_week IS '周内课程序号（必填）';
COMMENT ON COLUMN lessons.lesson_index_global IS '学期全局课程序号（必填）';
COMMENT ON COLUMN lessons.avg_head_up_rate IS '课堂平均抬头率，建议值域0~1（NUMERIC(5,4)支持两位或四位小数）';
COMMENT ON COLUMN lessons.score_high_order IS '课时高阶性分值（0~100）';
COMMENT ON COLUMN lessons.score_innovation IS '课时创新性分值（0~100）';
COMMENT ON COLUMN lessons.score_fun_experience IS '课时趣味体验分值（0~100）';
COMMENT ON COLUMN lessons.score_challenge IS '课时挑战度分值（0~100）';
COMMENT ON COLUMN lessons.score_ideology IS '课时课程思政分值（0~100）';
COMMENT ON COLUMN lessons.status IS '0=pending, 1=ready, 2=analyzing, 3=success, 4=failed';
COMMENT ON COLUMN lessons.analysis_updated_at IS '该课时最近一次分析结果写入完成时间';

-- ============================================
-- 3) ASR 原始数据表
-- ============================================
CREATE TABLE IF NOT EXISTS lesson_asr_payloads (
    lesson_ref_id UUID PRIMARY KEY REFERENCES lessons(id) ON DELETE CASCADE,
    asr_json JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE lesson_asr_payloads IS 'ASR 原始数据底座（按课时一行存储完整 JSONB）';
COMMENT ON COLUMN lesson_asr_payloads.lesson_ref_id IS '关联 lessons.id';
COMMENT ON COLUMN lesson_asr_payloads.asr_json IS '上游透传 ASR 结构化原文';

-- ============================================
-- 4) OCR 切片表
-- ============================================
CREATE TABLE IF NOT EXISTS ocr_segments (
    id BIGSERIAL PRIMARY KEY,
    lesson_ref_id UUID NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    time_offset INT NOT NULL CHECK (time_offset >= 0), -- 秒级偏移（用于和ASR对齐）
    page_num INT NOT NULL CHECK (page_num > 0),
    ocr_content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ocr_segments_lesson_ref_id ON ocr_segments(lesson_ref_id);
CREATE INDEX IF NOT EXISTS idx_ocr_segments_lesson_offset ON ocr_segments(lesson_ref_id, time_offset);

COMMENT ON TABLE ocr_segments IS 'OCR切片表（可按 time_offset 与 ASR 片段对齐）';
COMMENT ON COLUMN ocr_segments.lesson_ref_id IS '关联 lessons.id';
COMMENT ON COLUMN ocr_segments.time_offset IS 'OCR片段相对课时起点的秒级偏移（必填）';
COMMENT ON COLUMN ocr_segments.page_num IS '课件页码（必填）';

-- ============================================
-- 5) 课程词库表（思政/前沿）
-- ============================================
CREATE TABLE IF NOT EXISTS quality_taxonomy_terms (
    id UUID PRIMARY KEY,
    course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    term_type VARCHAR(16) NOT NULL CHECK (term_type IN ('ideology', 'innovation')),
    category_name VARCHAR(128) NOT NULL,
    keyword VARCHAR(128) NOT NULL,
    embedding VECTOR(1024),
    confidence NUMERIC(5,4) NOT NULL DEFAULT 0 CHECK (confidence >= 0 AND confidence <= 1),
    evidence_lessons INT NOT NULL DEFAULT 0 CHECK (evidence_lessons >= 0),
    evidence_weeks INT NOT NULL DEFAULT 0 CHECK (evidence_weeks >= 0),
    first_seen_week INT CHECK (first_seen_week IS NULL OR first_seen_week > 0),
    last_seen_week INT CHECK (last_seen_week IS NULL OR last_seen_week > 0),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(course_id, term_type, category_name, keyword)
);

CREATE INDEX IF NOT EXISTS idx_quality_terms_course_type ON quality_taxonomy_terms(course_id, term_type);
CREATE INDEX IF NOT EXISTS idx_quality_terms_course_category ON quality_taxonomy_terms(course_id, category_name);
CREATE INDEX IF NOT EXISTS idx_quality_terms_last_seen_week ON quality_taxonomy_terms(course_id, last_seen_week);
CREATE INDEX IF NOT EXISTS idx_quality_terms_embedding ON quality_taxonomy_terms USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

COMMENT ON TABLE quality_taxonomy_terms IS '课程级词库（思政/前沿），支持冷启动与周增量更新';
COMMENT ON COLUMN quality_taxonomy_terms.term_type IS '词库类型：ideology=思政，innovation=前沿';
COMMENT ON COLUMN quality_taxonomy_terms.first_seen_week IS '词条首次被命中的周次';
COMMENT ON COLUMN quality_taxonomy_terms.last_seen_week IS '词条最近一次被命中的周次';
COMMENT ON COLUMN quality_taxonomy_terms.evidence_lessons IS '词条累计命中的课时数';
COMMENT ON COLUMN quality_taxonomy_terms.evidence_weeks IS '词条累计命中的周数';

-- ============================================
-- 6) 学期画像任务表
-- ============================================
CREATE TABLE IF NOT EXISTS analysis_tasks (
    id UUID PRIMARY KEY,
    course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    task_kind VARCHAR(20) NOT NULL CHECK (task_kind IN ('week_profile', 'semester_profile')),
    target_week INT CHECK (target_week IS NULL OR target_week > 0),
    status SMALLINT NOT NULL DEFAULT 0 CHECK (status IN (0, 1, 2, 3, 4)),
    force_run BOOLEAN NOT NULL DEFAULT FALSE,
    dedupe_key VARCHAR(200) NOT NULL,
    requeue_needed BOOLEAN NOT NULL DEFAULT FALSE,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    current_node VARCHAR(128),
    graph_state JSONB,
    attempts INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INT NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    failed_reason TEXT,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    cancelled_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_analysis_tasks_course_id ON analysis_tasks(course_id);
CREATE INDEX IF NOT EXISTS idx_analysis_tasks_status ON analysis_tasks(status);
CREATE INDEX IF NOT EXISTS idx_analysis_tasks_kind_week ON analysis_tasks(task_kind, target_week);
CREATE INDEX IF NOT EXISTS idx_analysis_tasks_created_at ON analysis_tasks(created_at DESC);

-- 活动任务（queued/running）按 dedupe_key 串行去重
CREATE UNIQUE INDEX IF NOT EXISTS uq_analysis_tasks_active_dedupe
ON analysis_tasks(dedupe_key)
WHERE status IN (0, 1);

COMMENT ON TABLE analysis_tasks IS '周/学期画像任务表';
COMMENT ON COLUMN analysis_tasks.task_kind IS '任务类型：week_profile=周画像，semester_profile=学期画像';
COMMENT ON COLUMN analysis_tasks.status IS '0=queued, 1=running, 2=success, 3=failed, 4=cancelled';
COMMENT ON COLUMN analysis_tasks.dedupe_key IS '任务串行去重键：同键任务仅允许一个处于queued/running';
COMMENT ON COLUMN analysis_tasks.requeue_needed IS '运行中若收到同键触发，置为true并在完成后补跑一次';
COMMENT ON COLUMN analysis_tasks.cancel_requested IS '取消请求标记，worker在节点边界检查并终止';
COMMENT ON COLUMN analysis_tasks.graph_state IS '任务图运行快照(JSON)，记录节点进度、重试计数等';

-- ============================================
-- 7) 任务审计事件表
-- ============================================
CREATE TABLE IF NOT EXISTS analysis_task_events (
    id BIGSERIAL PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES analysis_tasks(id) ON DELETE CASCADE,
    level VARCHAR(16) NOT NULL CHECK (level IN ('info', 'warn', 'error')),
    node_name VARCHAR(128),
    event_type VARCHAR(64),
    detail JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_analysis_task_events_task_id ON analysis_task_events(task_id);
CREATE INDEX IF NOT EXISTS idx_analysis_task_events_created_at ON analysis_task_events(created_at DESC);

COMMENT ON TABLE analysis_task_events IS '任务审计日志（可解释审计：节点、事件、错误详情）';
COMMENT ON COLUMN analysis_task_events.event_type IS '事件类型：started/retry/failed/completed/cancelled等';

-- ============================================
-- 8) 模块报表数据表（前端直出）
-- ============================================
CREATE TABLE IF NOT EXISTS ai_analysis_reports (
    id UUID PRIMARY KEY,
    course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    report_level VARCHAR(16) NOT NULL CHECK (report_level IN ('lesson', 'week', 'semester')),
    target_id VARCHAR(80) NOT NULL,
    module_name VARCHAR(64) NOT NULL,
    report_data JSONB NOT NULL,
    source_task_id UUID REFERENCES analysis_tasks(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(course_id, report_level, target_id, module_name)
);

CREATE INDEX IF NOT EXISTS idx_ai_reports_course_level_target ON ai_analysis_reports(course_id, report_level, target_id);
CREATE INDEX IF NOT EXISTS idx_ai_reports_module_name ON ai_analysis_reports(module_name);
CREATE INDEX IF NOT EXISTS idx_ai_reports_updated_at ON ai_analysis_reports(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_reports_data_gin ON ai_analysis_reports USING gin (report_data);

COMMENT ON TABLE ai_analysis_reports IS 'AI分析结果仓（按模块存JSONB，供前端看板直出）';
COMMENT ON COLUMN ai_analysis_reports.report_level IS '报告层级：lesson/week/semester';
COMMENT ON COLUMN ai_analysis_reports.target_id IS '层级目标ID：lesson_id或week标识或course_id';
COMMENT ON COLUMN ai_analysis_reports.module_name IS '模块名：radar/ideology_map/bloom_evolution等';
COMMENT ON COLUMN ai_analysis_reports.report_data IS '模块渲染数据JSON（ECharts/看板直连数据）';
