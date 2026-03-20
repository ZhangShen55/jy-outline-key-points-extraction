import re
from pathlib import Path
from app.core.logging_config import get_logger
logger = get_logger(__name__)


# 子点切割
def split_subpoints(output_dir):
    chapters_root = Path(output_dir) / "chapters"
    if not chapters_root.exists():
        logger.info(f"未找到 {chapters_root}，跳过子点切割")
        return

    # 通用：2 3 4 6 四小点
    targets = {
        "2": "基本要求",
        "3": "教学重点",
        "4": "教学难点",
        "6": "课程思政"
    }

    for chapter_file in chapters_root.glob("*.txt"):
        chapter_name = chapter_file.stem
        content = chapter_file.read_text(encoding="utf-8")

        sub_dir = chapters_root / chapter_name
        sub_dir.mkdir(exist_ok=True)

        for num, title in targets.items():
            # 1) 先尝试正常写法：数字+点/顿/空格+标题+换行
            pattern = (
                rf"(?:^|\n)\s*{re.escape(num)}\s*(?:[.．、]?)\s*{re.escape(title)}\s*[:：]?\s*\n"
                rf"(.*?)"
                rf"(?=\n\s*(?:\d+[.．、]?\s*\w|第\d+章|$))"   # 遇到下一数字或“第X章”或文件尾结束
            )
            m = re.search(pattern, content, flags=re.S)

            # 2) 如果 6.课程思政 没匹配到，再兜底：从“6.课程思政”行直接到文件末尾
            if not m and num == "6":
                pattern_last = rf"(?:^|\n)\s*{re.escape(num)}\s*(?:[.．、]?)\s*{re.escape(title)}\s*[:：]?\s*\n(.*)"
                m = re.search(pattern_last, content, flags=re.S | re.I)

            if not m:
                logger.info(f"{chapter_name} — 未找到 {num}.{title}")
                continue

            body = m.group(1).strip()
            out_file = sub_dir / f"{num}_{title}.txt"
            out_file.write_text(body, encoding="utf-8")
            logger.info(f"✅ {out_file}")
