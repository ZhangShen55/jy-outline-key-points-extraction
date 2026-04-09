#!/bin/bash
# 数据库初始化脚本

DB_HOST="localhost"
DB_PORT="54320"
DB_USER="postgres"
DB_NAME="syllabus_db"

echo "开始初始化数据库..."

# 执行建表 SQL
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f sql/01_create_tasks_table.sql
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f sql/02_create_syllabus_tables.sql
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f sql/03_create_quality_tables.sql

echo "✅ 数据库初始化完成"
