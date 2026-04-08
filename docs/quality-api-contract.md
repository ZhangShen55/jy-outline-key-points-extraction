# 质量画像 API 契约（冻结版 v1）

## 1. 目标与范围

本文定义质量画像模块的接口契约，用于前后端与上下游联调。  
仅定义协议与语义，不涉及具体实现。

Base Path：`/api/v1/quality`

---

## 2. 通用约定

### 2.1 请求约定

1. 所有接口使用 `application/json`。
2. 查询接口统一使用 `POST .../query`。
3. 上游传入课时序号采用双序号：
   - `lesson_index_in_week`（周内第几节）
   - `lesson_index_global`（学期全局第几节）

### 2.2 响应包结构

```json
{
  "code": 20000,
  "message": "ok",
  "data": {},
  "trace_id": "uuid"
}
```

字段说明：

1. `code`：业务码
2. `message`：运维可读消息（包含关键排查上下文）
3. `data`：业务数据（允许为 `null`）
4. `trace_id`：链路追踪 ID

### 2.3 双层状态码策略

1. HTTP 状态码用于协议层语义（如 400/404/409/500）。
2. `code` 用于业务层细分。
3. 特例：`module/query` 在“数据暂无/未就绪”场景返回 HTTP 200，用业务码区分。

---

## 3. 业务码字典

| 业务码 | 含义 |
|---|---|
| 20000 | 成功 |
| 20011 | No-op（请求合法，但状态未发生变化） |
| 20404 | 模块数据不存在 |
| 20410 | 数据未就绪（例如目标范围内课时尚未全部分析） |
| 40001 | 参数校验失败 |
| 40401 | course_id 不存在 |
| 40402 | lesson 不存在 |
| 40403 | task_id 不存在 |
| 40901 | lesson 已成功，不允许覆盖 |
| 40902 | lesson 正在处理，不允许重复提交 |
| 40903 | 同 dedupe_key 任务已在排队/运行 |
| 50001 | 服务器内部错误 |

---

## 4. 状态与枚举

### 4.1 lessons.status

- `0` pending
- `1` ready
- `2` analyzing
- `3` success
- `4` failed

### 4.2 analysis_tasks.status

- `0` queued
- `1` running
- `2` success
- `3` failed
- `4` cancelled

### 4.3 其他枚举

1. `task_kind`: `week_profile | semester_profile`
2. `report_level`: `lesson | week | semester`
3. `module_name`:
   - `radar`
   - `ideology_map`
   - `bloom_evolution`
   - `challenge_pace_trend`
   - `innovation_profile`
   - `atmosphere_cross_diagnosis`

---

## 5. 接口定义

## 5.1 数据接入

**POST** `/courses/data-ingestion`

### Request

```json
{
  "course_id": "uuid-xxx",
  "course_name": "高级软件工程",
  "academic_year": "2025-2026-1",
  "teacher": "陈教授团队",
  "total_weeks": 16,
  "total_lessons": 32,
  "lesson_id": "lesson-001",
  "week_number": 1,
  "lesson_index_in_week": 1,
  "lesson_index_global": 1,
  "avg_head_up_rate": 0.85,
  "asr_data": [
    {"bg": 0, "ed": 5, "role": "teacher", "text": "......", "emotion": "平淡", "speed": 180}
  ],
  "ocr_data": [
    {"time_offset": 12, "page_num": 3, "ocr_content": "......"}
  ]
}
```

### 规则

1. `week_number`、`lesson_index_in_week`、`lesson_index_global` 必传。
2. 不做“连续性”校验（允许周次或课次跳号），只做合法性（>0）与唯一性。
3. 幂等键：`(course_id, lesson_id)`。
4. 约束键：
   - `(course_id, week_number, lesson_index_in_week)` 唯一
   - `(course_id, lesson_index_global)` 唯一
5. `courses` 允许自动 upsert。
6. 若 lesson 状态：
   - `success`：拒绝覆盖（40901）
   - `ready/analyzing`：拒绝重复提交（40902）
   - `pending/failed`：允许覆盖并重新触发
7. 课程词库初始化逻辑：
   - 不是仅绑定 `week=1 && index=1`
   - 任何 ingestion 请求都检查课程词库是否存在
   - 缺失则触发初始化（基于 `course_name`）

### Response（成功，202）

```json
{
  "code": 20000,
  "message": "Data ingested successfully for course_id=uuid-xxx, lesson_id=lesson-001",
  "data": {
    "course_id": "uuid-xxx",
    "lesson_id": "lesson-001",
    "week_number": 1,
    "lesson_index_in_week": 1,
    "lesson_index_global": 1,
    "lesson_status": 1,
    "course_created": true,
    "lesson_action": "created",
    "taxonomy_action": "triggered"
  },
  "trace_id": "uuid"
}
```

---

## 5.2 触发学期画像

**POST** `/tasks/semester-profile/generate`

### Request

```json
{
  "course_id": "uuid-xxx",
  "target_week": null,
  "force": false
}
```

### 规则

1. `target_week=null` 时，服务端解析为该课程已有课时数据的最大 `week_number`。
2. 若课程暂无可用课时数据，返回冲突（建议 409 + 20410）。
3. 返回中的 `data.target_week` 必为解析后的整数，不返回 `null`。
4. `target_week_source`：
   - `request`
   - `resolved_latest`
5. 去重键：
   - `dedupe_key = "{course_id}:semester_profile:{target_week_or_latest}"`
6. 命中同键 `queued/running`：
   - 不并发创建新任务
   - 置已有任务 `requeue_needed=true`
   - 返回已有 `task_id`
7. `force=true` 表示允许重算已成功结果，但仍遵守串行去重。

### Response（202）

```json
{
  "code": 20000,
  "message": "Task accepted",
  "data": {
    "task_id": "uuid-task-123",
    "course_id": "uuid-xxx",
    "target_week": 3,
    "target_week_source": "resolved_latest",
    "status": 0,
    "status_name": "queued",
    "dedupe_hit": true,
    "requeue_needed": true,
    "force_run": false
  },
  "trace_id": "uuid"
}
```

---

## 5.3 查询任务状态

**POST** `/tasks/semester-profile/status/query`

### Request

```json
{
  "task_id": "uuid-task-123"
}
```

### Response（200）

```json
{
  "code": 20000,
  "message": "Status retrieved",
  "data": {
    "task_id": "uuid-task-123",
    "course_id": "uuid-xxx",
    "task_kind": "semester_profile",
    "target_week": 3,
    "target_week_source": "resolved_latest",
    "status": 1,
    "status_name": "running",
    "current_node": "aggregate_metrics",
    "progress_pct": 45,
    "cancel_requested": false,
    "requeue_needed": false,
    "force_run": false,
    "attempts": 1,
    "max_attempts": 3,
    "failed_reason": null,
    "created_at": "2026-04-08T10:00:00Z",
    "started_at": "2026-04-08T10:00:05Z",
    "finished_at": null,
    "cancelled_at": null,
    "updated_at": "2026-04-08T10:00:08Z"
  },
  "trace_id": "uuid"
}
```

### 字段语义

1. `current_node`：当前执行节点（示例：`validate_input`, `load_week_data`, `aggregate_metrics`, `taxonomy_update`, `llm_diagnosis`, `upsert_reports`, `finalize`）。
2. `progress_pct`：按节点完成比例估算（0~100）。
3. `attempts`：已执行尝试次数，初始 0，首次实际执行后为 1。

---

## 5.4 查询模块数据

**POST** `/courses/semester-profile/module/query`

### Request

```json
{
  "course_id": "uuid-xxx",
  "report_level": "semester",
  "target_identifier": "uuid-xxx",
  "module_name": "radar"
}
```

### 规则

1. `target_identifier` 对应关系：
   - `lesson`：`lesson_id`
   - `week`：周标识，约定使用数字字符串，如 `"3"`
   - `semester`：`course_id`

### Response 场景

#### A. 成功有数据（HTTP 200）

```json
{
  "code": 20000,
  "message": "Dashboard data retrieved",
  "data": {
    "course_id": "uuid-xxx",
    "report_level": "semester",
    "target_identifier": "uuid-xxx",
    "module_name": "radar",
    "report_payload": {
      "scores": {
        "high_order": 75,
        "innovation": 80,
        "fun_experience": 65,
        "ideology": 90,
        "challenge": 85
      },
      "overall_score": 79.0
    },
    "updated_at": "2026-04-08T10:00:08Z",
    "source_task_id": "uuid-task-123"
  },
  "trace_id": "uuid"
}
```

#### B. 无数据（HTTP 200）

```json
{
  "code": 20404,
  "message": "No report data for module=radar, level=week, target=3",
  "data": {
    "course_id": "uuid-xxx",
    "report_level": "week",
    "target_identifier": "3",
    "module_name": "radar",
    "report_payload": null
  },
  "trace_id": "uuid"
}
```

#### C. 数据未就绪（HTTP 200）

```json
{
  "code": 20410,
  "message": "Data not ready: lessons in target scope are not fully analyzed",
  "data": {
    "course_id": "uuid-xxx",
    "report_level": "semester",
    "target_identifier": "uuid-xxx",
    "module_name": "radar",
    "report_payload": null,
    "missing_summary": {
      "missing_weeks": [3, 6, 7]
    }
  },
  "trace_id": "uuid"
}
```

#### D. 参数错误（HTTP 400）

```json
{
  "code": 40001,
  "message": "Invalid report_level, expected lesson|week|semester",
  "data": null,
  "trace_id": "uuid"
}
```

#### E. 课程不存在（HTTP 404）

```json
{
  "code": 40401,
  "message": "course_id not found: uuid-xxx",
  "data": null,
  "trace_id": "uuid"
}
```

---

## 5.5 取消任务

**POST** `/tasks/cancel`

### Request

```json
{
  "task_id": "uuid-task-123"
}
```

### 规则

1. `queued`：直接转 `cancelled`。
2. `running`：置 `cancel_requested=true`，worker 节点边界安全退出。
3. `success/failed/cancelled`：返回 `200 + no-op`（不改变状态）。
4. 不支持“立即硬中断”。
5. `already-cancel-requested` 返回 `200`（幂等）。

### Response（200，已受理取消）

```json
{
  "code": 20000,
  "message": "Cancel request accepted",
  "data": {
    "task_id": "uuid-task-123",
    "status": 1,
    "status_name": "running",
    "cancel_requested": true
  },
  "trace_id": "uuid"
}
```

### Response（200，no-op）

```json
{
  "code": 20011,
  "message": "No-op: task already in terminal state (success)",
  "data": {
    "task_id": "uuid-task-123",
    "status": 2,
    "status_name": "success",
    "cancel_requested": false
  },
  "trace_id": "uuid"
}
```

### Response（404）

```json
{
  "code": 40403,
  "message": "task_id not found: uuid-task-xxx",
  "data": null,
  "trace_id": "uuid"
}
```

---

## 6. 运维排查增强约定

所有失败或异常响应 `message` 中建议至少包含：

1. `course_id`
2. `lesson_id` 或 `task_id`
3. `report_level`
4. `target_identifier`
5. `module_name`
6. 具体缺失/冲突类型（如 `course_not_found`、`data_not_ready`、`dedupe_hit`）

---

## 7. 兼容性与变更规则

1. 新增字段向后兼容（可选字段优先）。
2. 枚举变更需先更新本契约。
3. 任何状态码语义变更需同步更新 `code` 字典与示例响应。

