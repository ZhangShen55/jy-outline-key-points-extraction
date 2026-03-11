"""
课堂分析相关提示词
"""

CHAPTER_MATCH_SYSTEM = "你只回答合法 JSON"

CHAPTER_MATCH_USER_TEMPLATE = """你是一位课程教学分析专家。
给定课程大纲的章节列表和本节课的关键要点，判断本节课最可能对应哪些章节（允许多个）。

【大纲章节列表】
{chapters_summary}

【本节课关键要点】
{key_points}

请严格返回如下 JSON（单行），只返回 JSON 不要其他内容：
{{"matched_chapters": [{{"chapter": "章节标题", "num": 章节序号}}]}}

要求：
- matched_chapters 为数组，至少包含 1 个章节
- chapter 为大纲中的原始章节标题
- num 为章节序号（整数，若无法确定则填 0）
"""

SEGMENT_MATCH_SYSTEM = "你只回答合法 JSON"

SEGMENT_MATCH_USER_TEMPLATE = """你是一位{course}课程教学分析专家。
给定一段该课堂语音转写文本和章节四要点列表，判断该段文本是否与某个知识点匹配。

【段落信息】
seg_id: {seg_id}
bg: {bg}
ed: {ed}
文本内容：
{full_text}

【章节四要点列表（每项含 category/title/lexicon）】
{points_json}

判断规则：
1. 通过 lexicon（词库）中的关键词在文本中的出现情况，找出最可能匹配的知识点
2. 只有当文本内容与知识点的 lexicon 有明确关联时才返回匹配结果
3. 如果没有任何知识点与该段文本匹配，返回 null

匹配时严格返回如下 JSON（单行）：
{{"category":"<basic/keypoints/difficulty/politics>","title":"<知识点标题>","matched_lexicon":["<命中的关键词1>", "<命中的关键词2>"],"matched_segments":[{{"seg_id":"{seg_id}","text_snippet":"<文本内容中与知识点相关最关键的一句话>","match_level":"<优秀/深度/常规/浅层>","reason":"<匹配理由>"}}]}}

不匹配时返回：{{"no_match": true}}

只返回 JSON，不要其他内容。
"""
