"""ASR 后处理提示词。"""

ASR_SEGMENT_SUMMARY_SYSTEM = (
    "你是课堂授课内容摘要助手。"
    "请基于给定的ASR片段，提炼该片段的教学概要。"
    "要求客观、简洁，不要虚构。"
    "只返回合法JSON。"
)

ASR_SEGMENT_SUMMARY_USER_TEMPLATE = """课程名: {course_name}
时间范围: {start_sec}~{end_sec} 秒
ASR片段文本:
{asr_text}

请输出：
1) summary（20~80字）
2) keywords（3~8个课程相关关键词）
"""

ASR_SEGMENT_SUMMARY_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["summary", "keywords"],
    "properties": {
        "summary": {"type": "string", "minLength": 8, "maxLength": 120},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}


OCR_TERMS_SYSTEM = (
    "你是课程术语抽取与ASR纠错词表构建专家。"
    "请基于OCR内容提取与课程相关的核心专业词汇，"
    "并构造可能出现的误识别映射（wrong->correct）。"
    "误识别映射应覆盖：同音、近音、口误、首字母相同导致的短词误识别。"
    "优先输出课堂高频、容易混淆、且可直接用于替换的映射。"
    "不要输出与课程无关词汇。"
    "只返回合法JSON。"
)

OCR_TERMS_USER_TEMPLATE = """课程名: {course_name}
时间范围: {start_sec}~{end_sec} 秒
OCR文本:
{ocr_text}

OCR关键词:
{ocr_keywords_json}

请输出：
1) core_terms（课程相关核心术语数组，可为空）
2) homophone_pairs（数组，元素含 wrong / correct）
"""

OCR_TERMS_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["core_terms", "homophone_pairs"],
    "properties": {
        "core_terms": {"type": "array", "items": {"type": "string"}},
        "homophone_pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["wrong", "correct"],
                "properties": {
                    "wrong": {"type": "string", "minLength": 1},
                    "correct": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


ASR_CORRECT_SYSTEM = (
    "你是ASR文本纠错与去口语冗余专家。"
    "目标是在不改变原意的前提下，输出可读、连贯、无明显噪声的文本。"
    "高优先级规则："
    "1) 只修正影响理解的错误：同音/近音误识别、错别字、明显口误。"
    "2) 必须清理无意义重复：连续重复字词、口吃碎片、重复短语。"
    "3) 允许修复口语断裂与语序错乱，但不得新增事实或改写观点。"
    "4) 术语优先：如与候选词表(core_terms_json/homophone_pairs_json)匹配，应优先替换为候选词。"
    "5) 条目严格对齐：输入输出条目一一对应，禁止增删。"
    "6) verify_role=false 时不要主动改角色。"
    "7) 只返回合法JSON，不要输出解释。"
)

ASR_CORRECT_USER_TEMPLATE = """课程名: {course_name}
时间范围: {start_sec}~{end_sec} 秒
上一段讲授概要（最多两段）:
{previous_summaries_json}
当前片段概要:
{segment_summary}
OCR术语词表:
{core_terms_json}
同音纠错参考:
{homophone_pairs_json}
是否需要角色复核: {verify_role}

待纠错ASR条目(JSON):
{asr_items_json}

纠错规则:
1. 只修正明显语音识别错误（同音字/近音字误识别、错别字）
2. 允许删除不影响语义的口语填充词
3. 必须清理无意义重复（连续重复字词、口吃碎片、重复短语）
4. 允许在不改变原意的前提下修复明显断句、语序和主语缺失问题
5. 必须保持条目数量不变，item_id必须一一对应
6. 不修改bg/ed，仅输出纠正后的文本与（可选）角色判断
7. 若 verify_role=false，不要主动改角色（可返回unknown）
8. 不要输出evidence等额外字段，只输出要求的JSON结构

请输出:
1) items（每条至少含 item_id / corrected_text；可选 corrected_role / confidence）
"""

ASR_CORRECT_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["item_id", "corrected_text"],
                "properties": {
                    "item_id": {"type": "string"},
                    "corrected_text": {"type": "string", "minLength": 1},
                    "corrected_role": {"type": "string", "enum": ["teacher", "student", "unknown"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


ASR_BOUNDARY_HEAD_SYSTEM = (
    "你是课堂开始时间边界判定助手。"
    "任务是从前5分钟候选片段里找出“进入稳定授课内容”的第一个锚点。"
    "必须从输入片段中选择锚点，不允许虚构时间。"
    "寒暄、点名、设备调试、闲聊、噪声、杂乱的交流内容不算正式开始。"
    "只返回合法JSON。"
)

ASR_BOUNDARY_HEAD_USER_TEMPLATE = """课程名: {course_name}
候选片段（来自整节课asr转写文本的前五分钟）:
{head_items_json}

请输出:
1) anchor_item_idx（必须来自输入idx）
2) start_bg_sec（通常等于该锚点bg，可直接引用）
3) evidence_item_indices（支持该判断的idx列表，0~6个）
4) reason_tags（从以下标签选择: teaching_start/greeting/device_debug/chatter/noise/transition）
5) model_confidence（0~1，表示你的主观把握）
6) insufficient_evidence（布尔；若证据不足则为true）
7) reason（简短说明，最多60字）
"""

ASR_BOUNDARY_HEAD_OUTPUT_SCHEMA = {
    "type": "object",
    "required": [
        "anchor_item_idx",
        "start_bg_sec",
        "evidence_item_indices",
        "reason_tags",
        "model_confidence",
        "insufficient_evidence",
        "reason",
    ],
    "properties": {
        "anchor_item_idx": {"type": "integer", "minimum": 0},
        "start_bg_sec": {"type": "number", "minimum": 0},
        "evidence_item_indices": {"type": "array", "items": {"type": "integer", "minimum": 0}},
        "reason_tags": {"type": "array", "items": {"type": "string"}},
        "model_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "insufficient_evidence": {"type": "boolean"},
        "reason": {"type": "string", "minLength": 1, "maxLength": 120},
    },
    "additionalProperties": False,
}


ASR_BOUNDARY_TAIL_SYSTEM = (
    "你是课堂结束时间边界判定助手。"
    "任务是从后5分钟候选片段里找出“最后一段稳定授课内容”的结束锚点。"
    "必须从输入片段中选择锚点，不允许虚构时间。"
    "结束后闲聊、离场、收设备、噪声、杂乱的交流内容不算正式授课内容。"
    "只返回合法JSON。"
)

ASR_BOUNDARY_TAIL_USER_TEMPLATE = """课程名: {course_name}
候选片段（来自整节课asr转写文本的后五分钟）:
{tail_items_json}

请输出:
1) anchor_item_idx（必须来自输入idx）
2) end_ed_sec（通常等于该锚点ed，可直接引用）
3) evidence_item_indices（支持该判断的idx列表，0~6个）
4) reason_tags（从以下标签选择: teaching_end/homework/summary/chatter/noise/transition）
5) model_confidence（0~1，表示你的主观把握）
6) insufficient_evidence（布尔；若证据不足则为true）
7) reason（简短说明，最多60字）
"""

ASR_BOUNDARY_TAIL_OUTPUT_SCHEMA = {
    "type": "object",
    "required": [
        "anchor_item_idx",
        "end_ed_sec",
        "evidence_item_indices",
        "reason_tags",
        "model_confidence",
        "insufficient_evidence",
        "reason",
    ],
    "properties": {
        "anchor_item_idx": {"type": "integer", "minimum": 0},
        "end_ed_sec": {"type": "number", "minimum": 0},
        "evidence_item_indices": {"type": "array", "items": {"type": "integer", "minimum": 0}},
        "reason_tags": {"type": "array", "items": {"type": "string"}},
        "model_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "insufficient_evidence": {"type": "boolean"},
        "reason": {"type": "string", "minLength": 1, "maxLength": 120},
    },
    "additionalProperties": False,
}
