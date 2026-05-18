"""教学活动类型细粒度分类提示词。"""

ACTIVITY_CLASSIFY_SYSTEM = (
    "你是课堂教学活动分析专家。"
    "请基于ASR与OCR片段判断每个时间片段的教学活动类型。"
    "只返回合法JSON，不要输出额外文字。"
)

ACTIVITY_CLASSIFY_USER_TEMPLATE = """课程名: {course_name}
活动类型定义:
1) theory_lecture: 纯理论讲授、概念阐释、知识点讲解
2) case_discussion: 案例引入、案例分析、情境讨论
3) teacher_student_interaction: 提问追问、学生回答、互动交流
4) experiment_explanation: 实验演示、操作步骤、结果观察与讲解

待分类片段(JSON):
{segment_items_json}

要求:
1) 对每个segment_id输出activity_type
2) confidence范围0~1
3) evidence_text提炼一句关键证据（<=40字）
"""

ACTIVITY_CLASSIFY_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["segment_id", "activity_type", "confidence", "evidence_text"],
                "properties": {
                    "segment_id": {"type": "string"},
                    "activity_type": {
                        "type": "string",
                        "enum": [
                            "theory_lecture",
                            "case_discussion",
                            "teacher_student_interaction",
                            "experiment_explanation",
                        ],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence_text": {"type": "string", "minLength": 1, "maxLength": 40},
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}

ACTIVITY_VERIFY_SYSTEM = (
    "你是课堂观察学生。"
    "请核验给定时间片段当前标签是否合理，并给出最终标签。"
    "只返回合法JSON。"
)

ACTIVITY_VERIFY_USER_TEMPLATE = """课程名: {course_name}
活动类型定义:
- theory_lecture: 纯理论讲授
- case_discussion: 案例探讨
- teacher_student_interaction: 师生互动
- experiment_explanation: 实验讲解

待核验片段(JSON):
{verify_items_json}

每条输出:
1) segment_id
2) final_activity_type
3) confidence(0~1)
4) keep_current_label(true/false)
5) reason(<=30字)
"""

ACTIVITY_VERIFY_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "segment_id",
                    "final_activity_type",
                    "confidence",
                    "keep_current_label",
                    "reason",
                ],
                "properties": {
                    "segment_id": {"type": "string"},
                    "final_activity_type": {
                        "type": "string",
                        "enum": [
                            "theory_lecture",
                            "case_discussion",
                            "teacher_student_interaction",
                            "experiment_explanation",
                        ],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "keep_current_label": {"type": "boolean"},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 30},
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}

