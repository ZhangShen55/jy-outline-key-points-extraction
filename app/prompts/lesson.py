"""
课堂分析相关提示词
"""

CHAPTER_MATCH_SYSTEM = "你只回答合法 JSON"

CHAPTER_MATCH_USER_TEMPLATE = """你是一位{course_name}课程教学分析专家。
给定课程大纲的章节列表和本节课的完整内容分析，判断本节课最可能对应哪些章节（允许多个）。

【大纲章节列表】
{chapters_summary}

【本节课内容分析】
主题：{overall_label}

关键要点：
{key_points}

各段落内容：
{skims_text}

知识结构：
{mindmap_text}

请严格返回如下 JSON（单行），只返回 JSON 不要其他内容：
{{"matched_chapters": [{{"chapter": "章节标题", "num": 章节序号}}]}}

要求：
- matched_chapters 为数组，至少包含 1 个章节
- chapter 为大纲中的原始章节标题
- num 为章节序号（整数，若无法确定则填 0）
- 综合考虑段落内容、知识结构中的具体概念和术语来判断最匹配的章节
"""

SEGMENT_MATCH_SYSTEM = "你只回答合法 JSON"

SEGMENT_MATCH_USER_TEMPLATE = """你是一位{course}课程教学分析专家。
给定一段该课堂语音转写文本和章节四要点列表，判断该段文本是否与某个知识点匹配。

【段落信息】
seg_id: {seg_id}
文本内容：
{full_text}

【章节四要点列表（每项含 category/title/lexicon）】
{points_json}

判断规则：
1. 通过 lexicon（词库）中的关键词在文本中的出现情况，找出最可能匹配的知识点
2. 只有当文本内容与知识点的 lexicon 有明确关联时才返回匹配结果
3. 如果没有任何知识点与该段文本匹配，返回 null

匹配时严格返回如下 JSON（单行）：
{{"category":"<basic/keypoints/difficulty/politics>","title":"<知识点标题>","matched_lexicon":["<命中的关键词1>", "<命中的关键词2>"],"matched_segments":[{{"seg_id":"{seg_id}","text_snippet":"<文本内容中与知识点相关最关键的一句话>","match_level":"<优秀/深度/常规/浅层>","reason":"<匹配理由,结合文本内容和知识点简要说明，不允许出现lexicon词库等字眼>"}}]}}

不匹配时返回：{{"no_match": true}}

只返回 JSON，不要其他内容。
"""

ALERTS_SYSTEM = "你只回答合法 JSON"

ALERTS_USER_TEMPLATE = """你是一位{course_name}课程教学质量分析专家。根据以下课堂分析数据，生成教学预警信息。

【本节课匹配章节】
第{chapters}章节

【整体覆盖情况】
{overall_json}

【分类覆盖情况】
{category_json}

【未覆盖的知识点】
{unmatched_json}

【匹配深度分布】
{depth_json}

请根据以上数据判断是否存在以下预警类型（仅选择确实存在问题的类型）：
- coverage_insufficient：整体知识点覆盖率偏低
- keypoints_uncovered：教学重点存在遗漏
- difficulty_uncovered：教学难点存在遗漏
- ideology_missing：课程思政内容缺失
- shallow_teaching：知识点讲授深度不足（浅层匹配占比过高）
- cross_chapter: 跨章节教学（匹配了多个章节，且章节跨度较大）
返回严格如下 JSON：
{{"type": ["<命中的预警类型>"], "message": "<预警描述>"}}

要求：
- type 为数组，仅包含确实命中的预警类型，只需要突出预警类型，不要超过两个元素；若无任何预警则返回空数组
- message 以「AI检测到本节课已进入第{chapters}章节的讲解」开头
- message 要简洁凝练，说明核心预警内容，并控制在2句话以内，不要出现人称词（我、你、他、您之类）
- 涉及多个知识点时用数量概括（如"3个教学重点暂未涉及"），不要逐一列举标题
- 语气客观温和
- 无预警时 message 简要表达课堂匹配良好即可
- 只返回 JSON，不要其他内容
"""
