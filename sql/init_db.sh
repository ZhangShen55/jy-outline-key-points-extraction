#!/bin/bash
# 数据库初始化脚本

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-54320}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-syllabus_db}"

QUALITY_DB_HOST="${QUALITY_DB_HOST:-$DB_HOST}"
QUALITY_DB_PORT="${QUALITY_DB_PORT:-$DB_PORT}"
QUALITY_DB_USER="${QUALITY_DB_USER:-$DB_USER}"
QUALITY_DB_NAME="${QUALITY_DB_NAME:-quality_db}"

echo "开始初始化数据库..."

# 主库（syllabus/task）
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f sql/01_create_tasks_table.sql
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f sql/02_create_syllabus_tables.sql

# 质量库（quality）
psql -h $QUALITY_DB_HOST -p $QUALITY_DB_PORT -U $QUALITY_DB_USER -d $QUALITY_DB_NAME -f sql/03_create_quality_tables.sql

echo "✅ 数据库初始化完成"
