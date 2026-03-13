"""
系统常量定义
"""

# ─── 任务状态码 ───────────────────────────────────────────────────────────────
class TaskStatus:
    """任务状态码"""
    COMPLETED = 0   # 已完成
    PENDING = 1     # 待处理
    QUEUED = 2      # 排队中
    PROCESSING = 3  # 处理中
    FAILED = 4      # 失败

    @classmethod
    def to_str(cls, code: int) -> str:
        """状态码转字符串"""
        mapping = {
            cls.COMPLETED: "completed",
            cls.PENDING: "pending",
            cls.QUEUED: "queued",
            cls.PROCESSING: "processing",
            cls.FAILED: "failed",
        }
        return mapping.get(code, "unknown")

    @classmethod
    def from_str(cls, status: str) -> int:
        """字符串转状态码"""
        mapping = {
            "completed": cls.COMPLETED,
            "pending": cls.PENDING,
            "queued": cls.QUEUED,
            "processing": cls.PROCESSING,
            "failed": cls.FAILED,
        }
        return mapping.get(status.lower(), cls.PENDING)


# ─── 任务类型 ─────────────────────────────────────────────────────────────────
class TaskType:
    """任务类型"""
    SYLLABUS = "syllabus"  # 大纲提取
    LESSON = "lesson"      # 课堂分析


# ─── 处理阶段 ─────────────────────────────────────────────────────────────────
class ProcessStage:
    """处理阶段"""
    # 大纲提取阶段
    PARSING = "parsing"         # 文档解析
    SPLITTING = "splitting"     # 章节分割
    EXTRACTING = "extracting"   # LLM 提取
    GENERATING = "generating"   # 生成词库

    # 课堂分析阶段
    MINDMAP = "mindmap"         # 生成脑图
    MATCHING = "matching"       # 章节匹配
    ANALYZING = "analyzing"     # 段落分析
    SUMMARIZING = "summarizing" # 汇总结果
