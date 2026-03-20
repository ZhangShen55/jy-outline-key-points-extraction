import aspose.words as aw
from pathlib import Path


def convert_office_to_pdf(doc_path: str, output_dir: str = None) -> Path:
    """使用 Aspose.Words 将 Office 文档转换为 PDF。"""
    doc_path = Path(doc_path)
    if not doc_path.exists():
        raise FileNotFoundError(f"File not found: {doc_path}")

    name_without_suff = doc_path.stem
    base_output_dir = Path(output_dir or doc_path.parent / "pdf_output")
    base_output_dir.mkdir(parents=True, exist_ok=True)

    final_pdf_path = base_output_dir / f"{name_without_suff}.pdf"

    try:
        doc = aw.Document(str(doc_path))
        doc.save(str(final_pdf_path))
        return final_pdf_path
    except Exception as e:
        raise RuntimeError(f"Aspose.Words conversion failed: {str(e)}")
