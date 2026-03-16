"""
数据验证工具
"""
import re
from typing import List, Tuple


# 允许的类别
VALID_CATEGORIES = {"basic", "keypoints", "difficulty", "politics"}

# 词库限制
MAX_LEXICONS_PER_POINT = 25  # 每个知识点最多25个词库
MAX_CHINESE_LENGTH = 10  # 中文最大长度
MIN_CHINESE_LENGTH = 2   # 中文最小长度
MAX_ENGLISH_WORD_LENGTH = 8  # 英文单词数最大
MAX_ENGLISH_LETTERS = 20  # 单个英文单词最大字母数


def validate_category(category: str) -> Tuple[bool, str]:
    """验证类别是否合法"""
    if category not in VALID_CATEGORIES:
        return False, f"类别必须是以下之一: {', '.join(VALID_CATEGORIES)}"
    return True, ""


def validate_lexicon_term(term: str) -> Tuple[bool, str]:
    """验证单个词库项"""
    if not term or not term.strip():
        return False, "词库项不能为空"

    term = term.strip()

    # 检查是否包含中文
    has_chinese = bool(re.search(r'[\u4e00-\u9fff]', term))

    if has_chinese:
        # 中文或中文+数字/字母混合
        # 计算实际字符长度（中文算1个字符）
        length = len(term)
        if length < MIN_CHINESE_LENGTH or length > MAX_CHINESE_LENGTH:
            return False, f"中文词库长度必须在{MIN_CHINESE_LENGTH}-{MAX_CHINESE_LENGTH}字之间"
    else:
        # 纯英文，按单词数计算
        words = term.split()
        if len(words) > MAX_ENGLISH_WORD_LENGTH:
            return False, f"英文词库最多{MAX_ENGLISH_WORD_LENGTH}个单词"

        # 检查单个单词长度
        for word in words:
            if len(word) > MAX_ENGLISH_LETTERS:
                return False, f"单个英文单词不能超过{MAX_ENGLISH_LETTERS}个字母"

    return True, ""


def validate_lexicons(lexicons: List[str]) -> Tuple[bool, str, List[str]]:
    """
    验证词库列表
    返回: (是否有效, 错误信息, 去重后的词库列表)
    """
    if not lexicons:
        return False, "词库列表不能为空", []

    # 去重并去除空值
    unique_terms = []
    seen = set()

    for term in lexicons:
        if not term or not term.strip():
            continue

        term = term.strip()

        # 验证单个词库项
        valid, msg = validate_lexicon_term(term)
        if not valid:
            return False, f"词库项 '{term}' 无效: {msg}", []

        # 去重
        if term not in seen:
            seen.add(term)
            unique_terms.append(term)

    if not unique_terms:
        return False, "词库列表不能全为空", []

    return True, "", unique_terms
