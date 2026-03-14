-- 任务表
CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(50) UNIQUE NOT NULL,
    task_type VARCHAR(20) NOT NULL,
    status SMALLINT NOT NULL DEFAULT 1,

    filename VARCHAR(255),
    file_size INTEGER,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    failed_at TIMESTAMP,
    elapsed_time FLOAT,

    error TEXT,
    result JSONB,
    extra_data JSONB,

    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_tasks_task_id ON tasks(task_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_type ON tasks(task_type);
CREATE INDEX idx_tasks_created_at ON tasks(created_at DESC);

COMMENT ON TABLE tasks IS '任务表';
COMMENT ON COLUMN tasks.status IS '0=completed, 1=pending, 2=queued, 3=processing, 4=failed';
COMMENT ON COLUMN tasks.task_type IS 'syllabus=大纲提取, lesson=课堂分析';
