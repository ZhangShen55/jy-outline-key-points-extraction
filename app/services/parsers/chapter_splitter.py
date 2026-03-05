from pathlib import Path


# -------------- 新增：基于传统方法的章节切割 --------------
def extract_chapters_by_traditional_method(full_text: str, output_dir: str):
    """
    使用传统字符串方法定位并切割章节
    定位"第X章"字样，将每个章节内容保存为单独文件
    """
    import re
    from app.core.logging_config import get_logger
    logger = get_logger(__name__)

    # 创建章节输出目录
    chapter_dir = Path(output_dir) / "chapters"
    chapter_dir.mkdir(exist_ok=True)

    # 使用正则表达式查找所有"第X章"的位置
    # 匹配模式：第 + 数字 + 章 + 可选空格 + 可选章节标题
    chapter_pattern = r'(第[123456789\d]+章)[\s：:]*([^\n]*)'
    matches = list(re.finditer(chapter_pattern, full_text))

    logger.info(f"找到 {len(matches)} 个章节标题")

    if not matches:
        logger.info("未找到章节标题，保存整个文档")
        # 如果没有找到章节，保存整个文本
        output_file = chapter_dir / "全文内容.txt"
        output_file.write_text(full_text, encoding="utf-8")
        return [str(output_file)]

    # 提取每个章节的内容
    chapter_files = []
    for i, match in enumerate(matches):
        chapter_title = match.group(1).strip()  # 例如："第1章"
        chapter_name = match.group(2).strip() if match.group(2) else ""  # 章节名称

        # 确定本章节的开始位置
        start_pos = match.start()

        # 确定本章节的结束位置（下一章的开始位置，或者是文本末尾）
        if i < len(matches) - 1:
            end_pos = matches[i + 1].start()
        else:
            end_pos = len(full_text)

        # 提取本章节内容
        chapter_content = full_text[start_pos:end_pos].strip()

        # 生成文件名：第1章_章节名称.txt
        if chapter_name:
            safe_chapter_name = re.sub(r'[\\/*?:"<>|]', "_", chapter_name)
            filename = f"{chapter_title}_{safe_chapter_name}.txt"
        else:
            filename = f"{chapter_title}.txt"

        # 保存章节内容
        output_file = chapter_dir / filename
        output_file.write_text(chapter_content, encoding="utf-8")
        chapter_files.append(str(output_file))

        logger.info(f"✅ 已保存: {filename} (长度: {len(chapter_content)} 字符)")

    return chapter_files