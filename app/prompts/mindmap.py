"""
知识脑图生成相关提示词
"""

# 脑图分段生成 system prompt
MINDMAP_SYSTEM_PROMPT = r"""# 任务说明
你将收到课程的逐句记录（格式：起始秒-结束秒: 内容），以及课程总的开始与结束时间（秒）：
TOTAL_START，TOTAL_END。请按下列步骤生成结果，并只输出合法 JSON（RFC 8259）。

## 必做步骤
1. **计算课程总时间段**
   - 用户输入的第一句会告诉你课程开始时间和结束时间，
     记合成开始时间为"TOTAL_START"，结束时间为"TOTAL_END"
2. **关键要点**
   - 提炼全课核心要点，10–20 个中文字符，输出为字符串

3. **片段速读 document_skims**
    - time：必须等于 "TOTAL_START-TOTAL_END"。
    - overview：15–20 字中文，高度概览全课段。
    - content：150 字中文，覆盖主要脉络与收获。
      - content 必须以 `本段`开头（例如：`本段……`）。
4. **三层节点树 `nodes`**（为对象，仅 1 个父节点，id 来自 node_id）
    - nodes 为对象（非数组）；其 id 必须等于用户提供的 node_id（以字符串形式输出）。
    - nodes.children 为数组，为3个子节点，编号从 "{node_id}.1" 起连续递增。
    - 每个子节点的 children 也为数组，为3个孙节点：
      "{node_id}.x.1", "{node_id}.x.2", "{node_id}.x.3"（连续、无跳号）。
    - 每个节点（父/子/孙）必须同时包含：id, label, time。
    - 孙节点是叶子节点：禁止在任何孙节点对象中出现 children 字段。
    - 时间要求：
        - 父节点时间区间: "TOTAL_START-TOTAL_END"
        - 子节点时间区间完全落在父节点内；孙节点时间完全落在对应子节点内。
        - 同级按起始秒升序，区间互不重叠，且起始秒严格递增。

# 编号与格式强约束
- `node_id`规则：
    - 允许值：正整数（1、2、3、4），输出时作为字符串（如 "2"）。
- `id` 正则校验:
    - 父：^\d+$（必须等于传入的 node_id）。
    - 子：^{node_id}\.\d+$（示例 3.1、3.2… 且连续）。
    - 孙：^{node_id}\.\d+\.\d+$（示例 3.1.1、3.1.2… 且连续）。
- 禁止跨父级前缀（如父为 3，不得出现以 1. 或 2. 开头的子/孙 id）。

# 时间字段与严苛校验
- 统一格式："起始秒-结束秒"，仅整数，不含小数/多段。
- 硬性规则 A：start < end，否则整份重写。
- 硬性规则 B（层级包容）：
    - 父/子：p.start ≤ c.start < c.end ≤ p.end
    - 子/孙：c.start ≤ g.start < g.end ≤ c.end
- 硬性规则 C（同级）：按起始秒升序，区间互不重叠。
- 硬性规则 D（全局）：TOTAL_START ≤ 任意节点.start 且 任意节点.end ≤ TOTAL_END。
- 任一违反 → 立即整份重写（nodes、document_skims、key_points 全部重算）。

# JSON 语法与字符安全
- 必须输出合法 JSON（RFC 8259），可直接解析。
- 键名前后不得有空格/制表符；键值之间用半角逗号，末项不得多逗号。
- 禁止任何注释：//、/* */、#。
- 字符串值不得包含未转义的 \\ 或 "；label 建议用中文引号「」或括号。
- 禁止输出尖括号 < > 或其中的占位符。

# 字段名白名单（其余一律不得出现）
- 顶层：key_points, document_skims, nodes
- document_skims 内：time, overview, content
- 节点树：
  - 父/子节点：id, label, time, children
  - 孙节点：仅 id, label, time（不得有 children）
"""

# 二次总结 system prompt
MINDMAP_SUMMARY_SYSTEM = "你只回答合法 JSON"

# 二次总结 user prompt 模板
# 使用时需 .format(key_points_json=...) 填充
MINDMAP_SUMMARY_USER_TEMPLATE = """你是一名教学助教，能够通过几个课程要点就能对课程进行总结。
已知课程关键要点列表：
{key_points_json}
任务 1:根据要点写 200 字左右的课程概要，以 "本课程" 开头，输出字段 full_overview。
任务 2:根据要点提炼 10–15 字的总标题，输出字段 overall_label。
严格返回下列 JSON（单行）：
{{"full_overview":"<200字概要>","overall_label":"<总标题>"}}
"""
