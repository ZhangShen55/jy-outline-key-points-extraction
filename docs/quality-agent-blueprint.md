# 学期综合质量画像系统开发蓝图（评审版）

## 1. 文档目的

本文给出“学期综合质量画像系统”的可实施开发蓝图，目标是在**不影响现有项目能力**（教学大纲提取、课堂匹配）的前提下，增量接入新能力：

- 多模态课时数据接入（ASR/OCR/抬头率等）
- 课时级特征分析（Lesson）
- 周级与学期级画像聚合（Week/Semester）
- 看板模块数据查询直出（JSONB）

本文是工程实施基线，覆盖架构、数据模型、状态机、接口、消息流、配置和分期计划。

---

## 2. 已确认决策（冻结）

1. 同仓库共用代码基座，但按进程拆分运行：`api`、`lesson-worker`、`semester-worker`。
2. 新任务使用独立表 `analysis_tasks`，不复用现有 `tasks`。
3. 查询接口统一使用 `POST .../query`（不使用 GET Body）。
4. `analysis_tasks.status` 增加 `CANCELLED`。
5. 同键任务采用“串行去重”：同一 `course_id + task_kind + target_week` 只允许一个运行实例。
6. `lesson_id` 仅在 `course_id` 内唯一（组合唯一）。
7. 上游按“单次推整节课数据”方式接入。
8. `lesson` 已成功（success）后再推送同课数据：直接拒绝。
9. 词库（思政/前沿）按周更新，收敛采用**物理删除**。
10. 强制重算通过接口参数 `force=true` 触发。
11. 原始数据保留天数、重试/补偿参数均放 `config.toml` 解耦。

---

## 3. 总体架构

### 3.1 逻辑分层

- `API 层`：接入、任务提交、任务状态查询、模块数据查询、任务取消。
- `服务层`：数据校验、幂等与状态流转、报表 Upsert、词库更新逻辑。
- `Worker 层`：
  - Lesson Worker：课时特征抽取与课时报表产出。
  - Semester Worker：周画像聚合、学期画像聚合与词库增量更新。
- `Agent 层`：LangGraph（后续接入）负责学期级多节点编排。
- `存储层`：PostgreSQL（关系数据 + JSONB + pgvector）。
- `消息层`：Kafka（触发、解耦、削峰、重试与死信）。

### 3.2 运行单元

- `api service`: HTTP 接口
- `worker-lesson`: 消费课时触发 topic
- `worker-semester`: 消费周/学期触发 topic

> 说明：避免“API + 大计算任务”同进程导致延迟抖动。

---

## 4. 领域模型与数据表

建议新增 SQL 文件：`sql/03_create_quality_tables.sql`。

### 4.1 核心实体

- `courses`：课程主信息
- `lessons`：课时主信息与处理状态
- `lesson_asr_payloads`：ASR 原始 JSONB
- `ocr_segments`：OCR 切片
- `quality_taxonomy_terms`：课程级词库（思政/前沿）
- `analysis_tasks`：周/学期任务调度与状态
- `analysis_task_events`：任务可解释审计日志
- `ai_analysis_reports`：看板模块结果仓（JSONB）

补充约束（本轮评审确认）：

1. `courses.teacher` 允许为 `NULL`（非强制字段）。
2. `lessons.avg_head_up_rate` 由上游透传，建议存储为 `0~1` 比例值。
3. `lessons` 增加 5 个独立分值字段（`0~100`，可空）：
   - `score_high_order`（高阶性）
   - `score_innovation`（创新性）
   - `score_fun_experience`（趣味体验）
   - `score_challenge`（挑战度）
   - `score_ideology`（课程思政）
4. `lesson_asr_payloads` 增加 `created_at` 与 `updated_at`。
5. `ocr_segments.time_offset`、`ocr_segments.page_num` 设为 `NOT NULL`（上游必传）。
6. `ocr_segments.ocr_keywords`：OCR 关键词列表。

### 4.2 关键约束

1. `lessons` 建组合唯一：`UNIQUE(course_id, week_number, lesson_index_in_week)` 与 `UNIQUE(course_id, lesson_index_global)`。
2. `analysis_tasks` 使用 `dedupe_key` 做“活动任务”唯一约束（仅 queued/running）。
3. `ai_analysis_reports` 建组合唯一：`UNIQUE(course_id, report_level, target_id, module_name)`，用于覆盖写入。

### 4.3 状态定义

#### lessons.status

- `0 pending`
- `1 ready`
- `2 analyzing`
- `3 success`
- `4 failed`

#### analysis_tasks.status

- `0 queued`
- `1 running`
- `2 success`
- `3 failed`
- `4 cancelled`

### 4.4 字段语义补充（重点）

1. `lessons.avg_head_up_rate NUMERIC(5,4)`：
   - 含义：总精度 5 位、小数 4 位，例如 `0.8520`、`0.6500`。
   - 上游若传两位小数（如 `0.85`）可直接入库，不冲突。
   - 推荐统一约定值域为 `0~1`。

2. `lessons.analysis_updated_at`：
   - 含义：该课时最近一次“分析结果完成写入”的时间戳。
   - 用途：增量触发判断、排障追踪、前端缓存刷新判断。

3. `lessons.score_*`（五类分值）：
   - 含义：lesson 粒度评分结果（`0~100`），由 lesson 分析任务回写。
   - 用途：作为周/学期雷达分聚合的数据底座（直接 `AVG`）。

4. `quality_taxonomy_terms.first_seen_week` / `last_seen_week`：
   - `first_seen_week`：词条首次在第几周被证据命中。
   - `last_seen_week`：词条最近一次被命中的周次。
   - 用途：支持“按周收敛删除”策略（物理删除前的判断依据）。

5. `analysis_tasks.dedupe_key`：
   - 含义：任务去重键，格式建议 `"{course_id}:{task_kind}:{target_week_or_latest}"`。
   - 用途：保证同键任务串行执行，避免并发重复计算。

6. `analysis_tasks.graph_state`（JSONB）：
   - 含义：任务运行中的节点状态快照（运行时上下文）。
   - 参考示例：
```json
{
  "target_week": 5,
  "completed_nodes": ["load_week_data", "aggregate_metrics"],
  "pending_nodes": ["llm_diagnosis", "write_reports"],
  "retry_count": 1
}
```

6. `ai_analysis_reports.report_data`（JSONB）：
   - 含义：给前端模块直接渲染的结构化数据载荷。
   - 参考示例（`module_name=radar`）：
```json
{
  "scores": {
    "high_order": 75,
    "innovation": 80,
    "fun_experience": 65,
    "ideology": 90,
    "challenge": 85
  },
  "overall_score": 79.0,
  "ai_diagnosis": "课程处于起步阶段，挑战度适中。"
}
```

### 4.5 字段命名说明（建议落库 COMMENT）

为避免“组合英文字段语义不清晰”，建议在 DDL 中补齐字段注释，至少覆盖：

1. `avg_head_up_rate`：课堂平均抬头率（0~1）。
2. `analysis_updated_at`：课时分析结果最近更新时间。
3. `dedupe_key`：任务串行去重键。
4. `graph_state`：任务图运行状态快照。
5. `report_data`：模块渲染数据载荷。
6. `first_seen_week`：词条首次命中周次。
7. `last_seen_week`：词条最近命中周次。

---

## 5. 幂等与串行去重

### 5.1 Lesson 数据推送幂等

同一 `course_id + lesson_id`：

- 若 lesson 状态为 `success`：拒绝写入，返回冲突提示。
- 若 lesson 状态为 `pending/failed`：允许覆盖数据并重触发。
- 若 lesson 状态为 `ready/analyzing`：返回“处理中”，不重复入队。

### 5.2 Task 串行去重

`dedupe_key = "{course_id}:{task_kind}:{target_week_or_latest}"`。

- 若同 `dedupe_key` 已有 `queued/running`，新触发不创建并发任务。
- 将已有任务标记 `requeue_needed=true`。
- 当前任务结束后，若 `requeue_needed=true`，自动再补跑一次（同 dedupe_key）。

---

## 6. Kafka 设计

### 6.1 Topics

- `quality.lesson.analysis.trigger.v1`
- `quality.profile.week.trigger.v1`
- `quality.profile.semester.trigger.v1`
- `quality.dlq.v1`

### 6.2 分区策略

- Message key 使用 `course_id`
- 保证同课程消息有序

### 6.3 重试与死信

- 可重试错误（超时、429、网络波动）：worker 内最多 3 次，指数退避。
- 不可重试错误（参数校验、业务规则冲突）：直接失败并记录原因。
- 超过重试阈值：写 `failed_reason`，投递 `quality.dlq.v1`。
- 定时补偿任务按配置扫描失败任务重投。

---

## 7. API 设计（新增）

统一前缀建议：`/api/v1/quality`。

### 7.1 数据接入

`POST /api/v1/quality/courses/data-ingestion`

- 功能：接收课时数据并触发 lesson 分析。
- 输入：`course_id, lesson_id, week_number, lesson_index_in_week, lesson_index_global, avg_head_up_rate, asr_data, ocr_data`
- 输出：接收成功（202）或冲突/校验失败。

### 7.2 触发学期画像

`POST /api/v1/quality/tasks/semester-profile/generate`

- 输入：`course_id`, `target_week`（可空，默认 latest）, `force`（可选）
- 输出：`task_id`、当前状态

### 7.3 查询任务状态

`POST /api/v1/quality/tasks/semester-profile/status/query`

- 输入：`task_id`
- 输出：`status`, `current_node`, `failed_reason`, `target_week`, `updated_at`

### 7.4 查询模块数据

`POST /api/v1/quality/courses/semester-profile/module/query`

- 输入：`course_id`, `report_level`, `target_identifier`, `module_name`
- 输出：`report_payload`（JSONB）

### 7.5 取消任务

`POST /api/v1/quality/tasks/cancel`

- 输入：`task_id`
- 动作：将任务置 `cancel_requested=true`，worker 在节点边界检查并退出为 `cancelled`

---

## 8. 模块定义（原型映射）

建议统一 `module_name`：

1. `radar`：两性一度综合雷达与总分
2. `ideology_map`：思政全局脉络与词云
3. `bloom_evolution`：认知层级演进
4. `challenge_pace_trend`：挑战度与节奏趋势
5. `innovation_profile`：前沿性/教学创新性
6. `atmosphere_cross_diagnosis`：氛围与趣味性跨维诊断

---

## 9. Worker 流程蓝图

### 9.1 Lesson Worker

1. 消费 `quality.lesson.analysis.trigger.v1`
2. 锁定 lesson，状态 `ready -> analyzing`
3. 生成 lesson 级模块：
   - `bloom`
   - `pace_challenge`
   - `ideology_innovation`
   - `atmosphere`
4. Upsert `ai_analysis_reports`（`report_level=lesson`）
5. lesson 状态置 `success`
6. 发送 week/semester 触发消息

### 9.2 Semester Worker（周画像）

1. 消费 `quality.profile.week.trigger.v1`
2. 聚合指定周 lesson 结果
3. 产出 week 级 6 模块
4. Upsert `ai_analysis_reports`（`report_level=week`）

### 9.3 Semester Worker（学期画像）

1. 消费 `quality.profile.semester.trigger.v1`
2. 读取 `1..target_week` 的 week 结果
3. 全量聚合并生成 semester 级 6 模块
4. Upsert `ai_analysis_reports`（`report_level=semester`）
5. 若 `requeue_needed=true`，自动补跑下一轮

---

## 10. 词库策略（课程名冷启动 + 周增量）

### 10.1 冷启动

- 输入仅 `course_name`
- LLM 生成首版：
  - 思政分类与词条
  - 前沿分类与词条
- 写入 `quality_taxonomy_terms`

### 10.2 周增量更新

每周执行：

1. 从该周 lesson 结果抽取候选词
2. 先匹配已有分类
3. 满足阈值则新增词/分类
4. 低证据词按规则物理删除

### 10.3 准入参数（配置化）

- `min_term_confidence`
- `min_term_evidence_lessons`
- `min_category_confidence`
- `min_category_evidence_weeks`
- `inactive_after_weeks`

### 10.4 联动重算

词库变更后，重算以下模块：

1. `ideology_map`
2. `innovation_profile`
3. `radar`（若评分依赖前两者）

---

## 11. 配置设计（config.toml）

`config.toml` 已增加注释模板（见当前仓库配置文件），后续代码接入读取：

- `[quality]`：开关、保留期、重试、补偿、取消检查
- `[quality.trigger]`：自动触发策略
- `[quality.taxonomy]`：词库更新阈值
- `[quality_database]`：质量画像独立数据库连接
- `[kafka]`：broker、topics、group id

---

## 12. 目录规划（新增）

建议新增目录与文件：

- `app/api/v1/endpoints/quality_ingestion.py`
- `app/api/v1/endpoints/quality_tasks.py`
- `app/api/v1/endpoints/quality_query.py`
- `app/services/quality/ingestion_service.py`
- `app/services/quality/report_service.py`
- `app/services/quality/taxonomy_service.py`
- `app/agents/quality_graph.py`
- `app/workers/lesson_worker.py`
- `app/workers/semester_worker.py`
- `app/models/quality_*.py`
- `app/prompts/quality/*.py`
- `sql/03_create_quality_tables.sql`

---

## 13. 与现有项目耦合评审

### 13.1 必须隔离项

1. 不修改现有 `/api/v1/document`、`/api/v1/lesson` 业务路径与契约。
2. 不复用现有 `tasks` 状态码。
3. 新旧 worker 进程隔离，避免资源争抢。

### 13.2 可复用项

1. 复用现有数据库连接、日志框架、配置加载框架。
2. 复用现有 LLM 调用封装能力（可抽公共 client）。
3. 复用 JSONB Upsert 的工程范式。

---

## 14. 分期实施计划

### Phase A：基础设施打底

- DDL 落库
- 新路由骨架
- Kafka Producer/Consumer 连通

### Phase B：Lesson 闭环

- `data-ingestion` 跑通
- lesson worker 产出 lesson 模块
- week/semester 触发链路打通

### Phase C：Week/Semester 闭环

- 串行去重
- 取消能力
- 覆盖写入 `ai_analysis_reports`

### Phase D：词库能力

- 课程名冷启动
- 周增量更新
- 物理删除收敛
- 词库变更联动重算

### Phase E：可解释与稳定性

- `analysis_task_events` 审计日志
- DLQ + 补偿任务
- 压测与性能优化

---

## 15. MVP 验收标准

1. 单节课推送后，lesson 模块可查（状态可追踪）。
2. 手动触发 semester 任务后，可查询完整状态流转。
3. 同键重复触发不并发执行。
4. `force=true` 可触发重算。
5. 取消任务可生效，状态为 `cancelled`。
6. 模块查询接口返回结构稳定、可直接驱动原型图渲染。

---

## 16. 风险与控制

1. 高峰期任务堆积：Kafka 分区 + worker 水平扩展。
2. LLM 成本波动：减少不必要节点重算，仅重算受影响模块。
3. 词库漂移：阈值准入 + 周更新 + 物理删除。
4. 可观测性不足：强制落任务事件与失败原因。

---

## 17. 下一步输出物

如本蓝图评审通过，下一步直接输出：

1. `sql/03_create_quality_tables.sql` 初版
2. 新接口 `schemas` 与 `endpoints` 骨架
3. `worker` 入口脚手架
4. Kafka 消息模型与去重器实现
5. 模块 `module_name` 与原型字段映射规范文档（前后端联调用）
