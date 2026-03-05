"""
文档处理管道服务
整合自 knowledge_graph/pipeline.py
"""
import os
import time
import asyncio
from pathlib import Path
import shutil
import re

from app.services.parsers.document_parser import parse_document_to_text
from app.services.parsers.chapter_splitter import extract_chapters_by_traditional_method
from app.services.parsers.subpoint_splitter import split_subpoints
from app.services.summarizer.summary_generator import extract_all_modules
from app.core.config import get_settings, get_parser_model_path
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def sanitize_filename(name: str) -> str:
    """清理文件名，移除非法字符"""
    return re.sub(r'[^\w\u4e00-\u9fff]+', '_', name)


async def run_pipeline(pdf_path: Path, orig_name: str = None) -> dict:
    """
    运行完整的文档处理管道

    Args:
        pdf_path: 文档路径
        orig_name: 原始文件名（可选）

    Returns:
        处理结果字典
    """
    settings = get_settings()

    # 设置 GPU
    os.environ["CUDA_VISIBLE_DEVICES"] = settings.CUDA_VISIBLE_DEVICES

    total_start = time.time()
    success = False

    # 创建输出目录
    out_dir = Path("output_all")
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 获取章节名
        if orig_name:
            chapter_name = sanitize_filename(orig_name)
        else:
            chapter_name = sanitize_filename(pdf_path.stem)

        logger.info(f"开始解析文档: {pdf_path}")

        # [1/4] 文档解析
        t1 = time.time()
        text_file = out_dir / f"{pdf_path.stem}_full.txt"

        if text_file.exists() and pdf_path.suffix.lower() != ".txt":
            logger.info("[1/4] 发现已存在的文本文件，跳过文档解析")
            full_text = text_file.read_text(encoding="utf-8")
        else:
            logger.info("[1/4] 文档解析 -> 提取纯文本中...")
            dolphin_path = get_parser_model_path()
            text_file = Path(await parse_document_to_text(str(pdf_path), dolphin_path, save_dir=out_dir))
            full_text = text_file.read_text(encoding="utf-8")

        logger.info(f"✅ 文档解析完成，耗时: {time.time() - t1:.2f}s")

        # [2/4] 章节切割
        t2 = time.time()
        logger.info("[2/4] 正在进行章节切割...")
        extract_chapters_by_traditional_method(full_text, out_dir)
        logger.info(f"✅ 章节切割完成，耗时: {time.time() - t2:.2f}s")

        # [3/4] 二级要点切割
        t3 = time.time()
        logger.info("[3/4] 正在进行二级要点切割...")
        split_subpoints(out_dir)
        logger.info(f"✅ 二级要点切割完成，耗时: {time.time() - t3:.2f}s")

        # [4/4] 调用 LLM 摘要生成
        t4 = time.time()
        logger.info("[4/4] 调用 LLM 摘要生成中...")
        triples, usage = extract_all_modules(out_dir)
        logger.info(f"✅ 摘要生成完成，耗时: {time.time() - t4:.2f}s")

        # 总结
        total_time = time.time() - total_start
        logger.info(f"🎯 全部流程完成，总耗时: {total_time:.2f}s")
        logger.info(f"📁 结果保存在: {out_dir}")

        result = {
            "model": settings.LLM_MODEL,
            "id": f"doc-{chapter_name}",
            "result": {
                "keywords": triples,
                "finished_time": int(time.time()),
                "process_time_ms": int(total_time * 1000),
                "finished_reason": "stop"
            },
            "usage": usage
        }

        success = True
        return result

    finally:
        if success:
            logger.info(f"✅ 成功，清理缓存目录: {out_dir}")
            shutil.rmtree(out_dir, ignore_errors=True)
        else:
            logger.warning(f"⚠️ 失败，缓存目录保留: {out_dir}")


if __name__ == "__main__":
    # 测试用
    asyncio.run(run_pipeline(Path("test.pdf")))
