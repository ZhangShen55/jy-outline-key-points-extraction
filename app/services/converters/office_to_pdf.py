# import subprocess
# import tempfile
# from pathlib import Path
# import platform
# import shutil


# def convert_office_to_pdf(doc_path: str, output_dir: str = None) -> Path:
#     doc_path = Path(doc_path)
#     if not doc_path.exists():
#         raise FileNotFoundError(f"File not found: {doc_path}")

#     name_without_suff = doc_path.stem
#     base_output_dir = Path(output_dir or doc_path.parent / "libreoffice_output")
#     base_output_dir.mkdir(parents=True, exist_ok=True)

#     with tempfile.TemporaryDirectory() as temp_dir:
#         temp_path = Path(temp_dir)
#         convert_cmd = [
#             "soffice", "--headless", "--convert-to", "pdf",
#             "--outdir", str(temp_path), str(doc_path)
#         ]
#         kwargs = {
#             "capture_output": True, "text": True, "timeout": 60,
#             "encoding": "utf-8", "errors": "ignore"
#         }
#         if platform.system() == "Windows":
#             kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

#         result = subprocess.run(convert_cmd, **kwargs)
#         if result.returncode != 0:
#             raise RuntimeError(f"LibreOffice failed: {result.stderr}")

#         pdf_path = next(temp_path.glob("*.pdf"))
#         final_pdf_path = base_output_dir / f"{name_without_suff}.pdf"
#         shutil.copy2(pdf_path, final_pdf_path)
#         return final_pdf_path



import aspose.words as aw
from pathlib import Path

def convert_office_to_pdf(doc_path: str, output_dir: str = None) -> Path:
    """
    使用 aspose-words 将 docx 转换为 pdf，保持原有的接口定义实现无感替换。
    """
    doc_path = Path(doc_path)
    if not doc_path.exists():
        raise FileNotFoundError(f"File not found: {doc_path}")

    # 确定输出目录
    name_without_suff = doc_path.stem
    # 保持原有的目录命名习惯，或者你也可以改为 "aspose_output"
    base_output_dir = Path(output_dir or doc_path.parent / "pdf_output")
    base_output_dir.mkdir(parents=True, exist_ok=True)

    # 最终生成的 PDF 路径
    final_pdf_path = base_output_dir / f"{name_without_suff}.pdf"

    try:
        # 加载文档 (自动支持 doc, docx, rtf 等)
        doc = aw.Document(str(doc_path))
        
        # 直接保存为 PDF
        # Aspose 会自动处理复杂的布局和中文字体渲染
        doc.save(str(final_pdf_path))
        
        return final_pdf_path
    except Exception as e:
        raise RuntimeError(f"Aspose.Words conversion failed: {str(e)}")
