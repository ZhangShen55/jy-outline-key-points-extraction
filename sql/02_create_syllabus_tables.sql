-- 启用 pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- 大纲主表
CREATE TABLE IF NOT EXISTS syllabuses (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(50) UNIQUE NOT NULL,
    course VARCHAR(255) NOT NULL,
    filename VARCHAR(255),
    raw_result JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_syllabuses_task_id ON syllabuses(task_id);
CREATE INDEX idx_syllabuses_course ON syllabuses(course);

-- 章节表
CREATE TABLE IF NOT EXISTS chapters (
    id SERIAL PRIMARY KEY,
    syllabus_id INTEGER NOT NULL REFERENCES syllabuses(id) ON DELETE CASCADE,
    chapter_num INTEGER,
    chapter_title VARCHAR(500) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_chapters_syllabus ON chapters(syllabus_id);

-- 知识点表
CREATE TABLE IF NOT EXISTS knowledge_points (
    id SERIAL PRIMARY KEY,
    chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    category VARCHAR(20) NOT NULL,
    title VARCHAR(500) NOT NULL,
    summary TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_kp_chapter ON knowledge_points(chapter_id);
CREATE INDEX idx_kp_category ON knowledge_points(category);

-- 词库表（带向量）
CREATE TABLE IF NOT EXISTS lexicons (
    id SERIAL PRIMARY KEY,
    knowledge_point_id INTEGER NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
    term VARCHAR(200) NOT NULL,
    embedding vector(1024),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_lexicons_kp ON lexicons(knowledge_point_id);
CREATE INDEX idx_lexicons_embedding ON lexicons USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

COMMENT ON TABLE syllabuses IS '大纲主表';
COMMENT ON TABLE chapters IS '章节表';
COMMENT ON TABLE knowledge_points IS '知识点表';
COMMENT ON TABLE lexicons IS '词库表（带向量）';
COMMENT ON COLUMN knowledge_points.category IS 'basic/keypoints/difficulty/politics';
