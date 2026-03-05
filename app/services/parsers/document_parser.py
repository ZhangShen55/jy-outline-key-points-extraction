import sys
import os
from pathlib import Path
import time
from app.services.converters.office_to_pdf import convert_office_to_pdf
from app.services.models.dolphin_model import DOLPHIN, process_document
from app.core.logging_config import get_logger

logger = get_logger(__name__)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))



async def parse_document_to_text(doc_path: str, model_path: str, save_dir: Path = None):
    """
    解析文档为纯文本，同时打印耗时。
    save_dir: 可选参数，指定中间文件存放目录
    """
    doc = Path(doc_path)
    if save_dir is None:
        save_dir = Path(__file__).resolve().parent / "output_all"
    save_dir.mkdir(parents=True, exist_ok=True)

    exts = [".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG",
            ".pdf", ".PDF", ".docx", ".doc", ".pptx", ".ppt",
            ".xls", ".xlsx", ".txt"]

    if not doc.suffix.lower() in exts:
        raise ValueError(f"Unsupported file type: {doc.suffix}")

    start_total = time.time()

    # ---------- 文档转换 / 读取 ----------
    if doc.suffix.lower() == ".txt":
        full_text = doc.read_text(encoding="utf-8")
        results = [{"elements": [{"text": full_text, "label": "text"}]}]
    else:
        if doc.suffix.lower() in [".docx", ".doc", ".pptx", ".ppt", ".xls", ".xlsx"]:
            file_path = convert_office_to_pdf(doc, output_dir=str(save_dir))
        else:
            file_path = doc


        # ---------- Dolphin 模型解析 ----------
        dolphin_model = DOLPHIN(model_path)
        start_pages = time.time()
        results = process_document(
            document_path=str(file_path),
            model=dolphin_model,
            save_dir=str(save_dir),
            max_batch_size=1,
        )

        # results = process_document(
        #     document_path=str(file_path),
        #     model=dolphin_model,
        #     save_dir=save_dir,  # 可以直接传 Path
        #     max_batch_size=1,
        #     max_workers=1,  # 并行进程数
        #     gpus="0,1"  # 使用哪些 GPU
        # )
        #

        pages_time = time.time() - start_pages
        logger.info(f"Dolphin 模型处理 {len(results)} 页,耗时: {pages_time:.2f}s")

        # 收集文本
        lines = []
        for page in results:
            for el in page["elements"]:
                if el.get("label") in {"fig", "tab"}:
                    continue # 跳过图片和表格
                txt = el.get("text", "").strip() # 去除首尾空白
                if txt: 
                    lines.append(txt)
        full_text = "\n".join(lines)

    # ---------- 写出文本 ----------
    full_text_file = save_dir / f"{doc.stem}_full.txt"
    full_text_file.write_text(full_text, encoding="utf-8")

    total_time = time.time() - start_total
    print(f"文档总处理耗时: {total_time:.2f}s")

    return str(full_text_file)
