import sys
import os
from pathlib import Path
import time
import pymupdf
from app.services.converters.office_to_pdf import convert_office_to_pdf
from app.core.logging_config import get_logger

logger = get_logger(__name__)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def parse_document_to_text(doc_path: str, save_dir: Path = None):
    """解析文档为纯文本并输出到文件。"""
    doc = Path(doc_path)
    if save_dir is None:
        save_dir = Path(__file__).resolve().parent / "output_all"
    save_dir.mkdir(parents=True, exist_ok=True)

    exts = [".pdf", ".PDF", ".docx", ".doc", ".pptx", ".ppt",
            ".xls", ".xlsx", ".txt"]

    if not doc.suffix.lower() in exts:
        raise ValueError(f"Unsupported file type: {doc.suffix}")

    start_total = time.time()

    # 转换或读取文档
    if doc.suffix.lower() == ".txt":
        full_text = doc.read_text(encoding="utf-8")
    else:
        if doc.suffix.lower() in [".docx", ".doc", ".pptx", ".ppt", ".xls", ".xlsx"]:
            file_path = convert_office_to_pdf(doc, output_dir=str(save_dir))
        else:
            file_path = doc

        # 使用 PyMuPDF 提取文本
        full_text = _extract_text_with_pymupdf(str(file_path))

    # 写出文本文件
    full_text_file = save_dir / f"{doc.stem}_full.txt"
    full_text_file.write_text(full_text, encoding="utf-8")

    total_time = time.time() - start_total
    logger.info(f"文档总处理耗时: {total_time:.2f}s")

    return str(full_text_file)


def _extract_text_with_pymupdf(pdf_path: str) -> str:
    """使用 PyMuPDF 从 PDF 中提取纯文本。"""
    text_lines = []
    try:
        doc = pymupdf.open(pdf_path)
        page_count = len(doc)
        for page_num in range(page_count):
            page = doc[page_num]
            text = page.get_text("text")
            if text.strip():
                text_lines.append(text.strip())
        doc.close()
        logger.info(f"PyMuPDF 提取 {page_count} 页文本完成")
    except Exception as e:
        logger.error(f"PyMuPDF 提取文本失败: {e}")
        raise

    return "\n".join(text_lines)
