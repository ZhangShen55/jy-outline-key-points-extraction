"""Bloom v2 相关提示词。"""

TEACHER_QUESTION_JUDGE_SYSTEM = (
    "你是本节课的授课教师本人。"
    "请判断候选问句更可能是老师提问还是学生提问。"
    "只返回合法JSON，不要输出任何额外文字。"
)

TEACHER_QUESTION_JUDGE_USER_TEMPLATE = """课程名: {course_name}
章节主题: {topic_hint}
候选问句时间: {start}-{end}
上一句: {prev_sentence}
本句: {candidate_question}
下一句: {next_sentence}
ASR角色(仅供参考，可能不准): prev={prev_role}, cur={cur_role}, next={next_role}

判断目标:
1) 该问句是否更可能是老师提问？
2) 给出teacher_probability和confidence，范围0~1
3) 给出一句简短理由（<=30字）
4) 输出规范化问句文本（修复明显ASR口误但不改变语义）
"""

TEACHER_BLOOM_SYSTEM = (
    "你是课程教学评估专家。"
    "请对每条老师提问按Bloom六级认知目标给出概率分布。"
    "六级为L1记忆 L2理解 L3应用 L4分析 L5评价 L6创造。"
    "每条记录l1~l6必须是整数且总和=100。"
    "只返回合法JSON。"
)

TEACHER_BLOOM_USER_TEMPLATE = """课程名: {course_name}
老师提问列表(JSON):
{questions_json}

请为每条提问输出:
1) l1~l6 六级占比(整数，总和100)
2) confidence(0~1)
3) evidence_text（直接引用该条提问核心句）
"""

TEACHER_BLOOM_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "sentence_id",
                    "l1",
                    "l2",
                    "l3",
                    "l4",
                    "l5",
                    "l6",
                    "confidence",
                    "evidence_text",
                ],
                "properties": {
                    "sentence_id": {"type": "string"},
                    "l1": {"type": "integer", "minimum": 0, "maximum": 100},
                    "l2": {"type": "integer", "minimum": 0, "maximum": 100},
                    "l3": {"type": "integer", "minimum": 0, "maximum": 100},
                    "l4": {"type": "integer", "minimum": 0, "maximum": 100},
                    "l5": {"type": "integer", "minimum": 0, "maximum": 100},
                    "l6": {"type": "integer", "minimum": 0, "maximum": 100},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence_text": {"type": "string", "minLength": 2},
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}

OCR_CLEAN_SYSTEM = (
    "你是课程课件制作老师。"
    "请清洗OCR结果，移除与课程无关、界面噪声和明显误识别内容。"
    "只返回合法JSON。"
)

OCR_CLEAN_USER_TEMPLATE = """课程名: {course_name}
课件片段(JSON):
{ocr_items_json}

请对每条片段输出:
1) keep: 是否保留
2) cleaned_content: 清洗后正文
3) cleaned_keywords: 清洗后关键词
4) relevance_score: 与教学内容相关度(0~1)
5) noise_tags: 噪声标签数组
"""

OCR_CLEAN_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "ocr_id",
                    "keep",
                    "cleaned_content",
                    "cleaned_keywords",
                    "relevance_score",
                    "noise_tags",
                ],
                "properties": {
                    "ocr_id": {"type": "string"},
                    "keep": {"type": "boolean"},
                    "cleaned_content": {"type": "string"},
                    "cleaned_keywords": {"type": "array", "items": {"type": "string"}},
                    "relevance_score": {"type": "number", "minimum": 0, "maximum": 1},
                    "noise_tags": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}

OCR_BLOOM_SYSTEM = (
    "你是课程教学评估专家。"
    "请基于清洗后的课件片段，判断Bloom六级分布。"
    "六级定义："
    "L1记忆=识记/回忆定义；"
    "L2理解=解释概念/说明意义；"
    "L3应用=套用方法/按步骤求解；"
    "L4分析=拆解结构/比较关系/判定边界条件；"
    "L5评价=基于标准作判断与优劣评估；"
    "L6创造=提出新方案/原创建模/开放式设计。"
    "重要约束："
    "“例题演算、公式推导、区域分解、板书步骤”通常属于L3/L4，不应直接判为L6；"
    "若无“提出新方案/创新设计/开放任务”证据，L6必须<=15；"
    "若无明确评价标准对比证据，L5不应过高。"
    "每条记录l1~l6必须是整数且总和=100。"
    "只返回合法JSON。"
)

OCR_BLOOM_USER_TEMPLATE = """课程名: {course_name}
清洗后课件片段(JSON):
{clean_ocr_items_json}

请为每条片段输出:
1) l1~l6 六级占比(整数，总和100)
2) confidence(0~1)
3) evidence_text（课件关键句，必须能支撑高阶判断）
4) 避免单级塌缩：除非证据非常充分，不要出现某一级=100%
"""

OCR_BLOOM_CALIBRATE_SYSTEM = (
    "你是严谨的教学督导专家。"
    "你将收到OCR分级初稿，其中部分片段可能把L6判得过高。"
    "请做保守校准："
    "若无创新设计证据，L6必须<=15；"
    "若无评价对比证据，L5应控制在合理范围；"
    "步骤演算/区域分解类内容优先分配到L3/L4。"
    "每条记录l1~l6整数且总和=100。"
    "只返回合法JSON。"
)

OCR_BLOOM_CALIBRATE_USER_TEMPLATE = """课程名: {course_name}
待校准片段(JSON):
{calibrate_items_json}

请逐条输出:
1) ocr_id
2) l1~l6(整数，总和100)
3) confidence(0~1)
4) evidence_text（简短证据）
"""

OCR_BLOOM_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "ocr_id",
                    "l1",
                    "l2",
                    "l3",
                    "l4",
                    "l5",
                    "l6",
                    "confidence",
                    "evidence_text",
                ],
                "properties": {
                    "ocr_id": {"type": "string"},
                    "l1": {"type": "integer", "minimum": 0, "maximum": 100},
                    "l2": {"type": "integer", "minimum": 0, "maximum": 100},
                    "l3": {"type": "integer", "minimum": 0, "maximum": 100},
                    "l4": {"type": "integer", "minimum": 0, "maximum": 100},
                    "l5": {"type": "integer", "minimum": 0, "maximum": 100},
                    "l6": {"type": "integer", "minimum": 0, "maximum": 100},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence_text": {"type": "string", "minLength": 2},
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}

BLOOM_INTERPRET_SYSTEM = (
    "你是课程教学督导专家。"
    "请基于Bloom统计结果输出专业、客观、简洁的解读。"
    "输出2~3句，不要空话。"
    "只返回合法JSON。"
)

BLOOM_INTERPRET_USER_TEMPLATE = """课程名: {course_name}
章节主题: {topic_hint}
teacher_distribution: {teacher_distribution}
ocr_distribution: {ocr_distribution}
overall_distribution: {overall_distribution}
bands: {bands}
权重: teacher={teacher_weight}, ocr={ocr_weight}
"""

BLOOM_INTERPRET_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["ai_interpretation"],
    "properties": {
        "ai_interpretation": {"type": "string", "minLength": 20, "maxLength": 220},
    },
    "additionalProperties": False,
}
