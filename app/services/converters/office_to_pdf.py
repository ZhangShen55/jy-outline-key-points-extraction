import subprocess
import tempfile
from pathlib import Path
import platform
import shutil


def convert_office_to_pdf(doc_path: str, output_dir: str = None) -> Path:
    doc_path = Path(doc_path)
    if not doc_path.exists():
        raise FileNotFoundError(f"File not found: {doc_path}")

    name_without_suff = doc_path.stem
    base_output_dir = Path(output_dir or doc_path.parent / "libreoffice_output")
    base_output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        convert_cmd = [
            "soffice", "--headless", "--convert-to", "pdf",
            "--outdir", str(temp_path), str(doc_path)
        ]
        kwargs = {
            "capture_output": True, "text": True, "timeout": 60,
            "encoding": "utf-8", "errors": "ignore"
        }
        if platform.system() == "Windows":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        result = subprocess.run(convert_cmd, **kwargs)
        if result.returncode != 0:
            raise RuntimeError(f"LibreOffice failed: {result.stderr}")

        pdf_path = next(temp_path.glob("*.pdf"))
        final_pdf_path = base_output_dir / f"{name_without_suff}.pdf"
        shutil.copy2(pdf_path, final_pdf_path)
        return final_pdf_path
