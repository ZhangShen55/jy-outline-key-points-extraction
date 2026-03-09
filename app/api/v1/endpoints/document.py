"""
文档处理端点
"""
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pathlib import Path
import tempfile
import uuid
import base64
import shutil
from datetime import datetime
from typing import Dict, Optional

from app.schemas.request import ProcessRequest
from app.schemas.response import TaskResponse, TaskStatusResponse
from app.core.logging_config import get_logger
from app.core.exceptions import NotFoundException

logger = get_logger(__name__)
router = APIRouter()

# 任务存储（生产环境应使用 Redis 或数据库）
tasks: Dict[str, dict] = {}

# 文件魔数 -> 格式映射
_MAGIC_BYTES = {
    b'%PDF': 'pdf',
    b'PK\x03\x04': 'docx',
    b'\xd0\xcf\x11\xe0': 'doc',
}


def detect_file_type(file_bytes: bytes) -> Optional[str]:
    """通过魔数检测文件类型，返回 'pdf' / 'docx' / 'doc' / None"""
    header = file_bytes[:4]
    for magic, fmt in _MAGIC_BYTES.items():
        if header.startswith(magic):
            return fmt
    return None


def prepare_pdf_base64(filedata: str, filename: str) -> str:
    """
    将接收到的 base64 转为 PDF base64：
    - PDF：直接返回原始 base64
    - doc/docx：写临时文件 -> aspose 转 PDF -> 读取编码 base64，成功或失败都清理临时文件
    """
    file_bytes = base64.b64decode(filedata)
    file_type = detect_file_type(file_bytes)

    if file_type is None:
        raise ValueError(f"不支持的文件格式，无法识别文件头: {filename}")

    if file_type == 'pdf':
        return filedata

    # doc / docx：需要转换
    tmp_dir = Path(tempfile.mkdtemp())
    suffix = '.docx' if file_type == 'docx' else '.doc'
    tmp_doc = tmp_dir / f"input{suffix}"
    try:
        tmp_doc.write_bytes(file_bytes)

        from app.services.converters.office_to_pdf import convert_office_to_pdf
        pdf_path = convert_office_to_pdf(str(tmp_doc), output_dir=str(tmp_dir))

        pdf_base64 = base64.b64encode(pdf_path.read_bytes()).decode('utf-8')
        return pdf_base64
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def process_document_background(task_id: str, filedata: str, orig_name: str):
    """后台处理文档的异步函数（LLM Pipeline）"""
    try:
        tasks[task_id]["status"] = "processing"
        tasks[task_id]["message"] = "文档处理中..."
        tasks[task_id]["started_at"] = datetime.now().isoformat()

        logger.info(f"🔄 开始处理任务 {task_id}")

        from app.services.llm_pipeline import run_llm_pipeline
        result = await run_llm_pipeline(filedata, orig_name)
        result["id"] = task_id

        tasks[task_id]["status"] = "completed"
        tasks[task_id]["result"] = result
        tasks[task_id]["message"] = "处理完成"
        tasks[task_id]["completed_at"] = datetime.now().isoformat()

        logger.info(f"✅ 任务 {task_id} 处理完成")

    except Exception as e:
        logger.error(f"❌ 任务 {task_id} 处理失败: {e}")
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)
        tasks[task_id]["message"] = f"处理失败: {e}"
        tasks[task_id]["failed_at"] = datetime.now().isoformat()


@router.post("/process", response_model=TaskResponse, status_code=202)
async def process_document(request: ProcessRequest, background_tasks: BackgroundTasks):
    """
    提交文档处理任务（异步）

    立即返回任务ID，不等待处理完成。
    使用 GET /document/status/{task_id} 查询处理进度。
    """
    try:
        task_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        orig_name = Path(request.filename).stem

        # 检测文件类型，doc/docx 转为 PDF base64
        pdf_filedata = prepare_pdf_base64(request.filedata, request.filename)

        tasks[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "filename": request.filename,
            "message": "任务已提交，等待处理...",
            "result": None,
            "error": None,
            "created_at": datetime.now().isoformat()
        }

        background_tasks.add_task(process_document_background, task_id, pdf_filedata, orig_name)

        logger.info(f"📝 任务 {task_id} 已提交，文件: {request.filename}")

        return TaskResponse(
            task_id=task_id,
            status="pending",
            message="任务已提交，请使用 GET /api/v1/document/status/{task_id} 查询处理进度"
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"❌ 提交任务失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """
    查询任务状态

    返回任务的当前状态和结果（如果已完成）
    """
    task = tasks.get(task_id)

    if not task:
        raise NotFoundException(message=f"任务 {task_id} 不存在")

    response = TaskStatusResponse(
        task_id=task_id,
        status=task["status"],
        message=task["message"],
        filename=task["filename"],
        created_at=task["created_at"],
        started_at=task.get("started_at"),
        completed_at=task.get("completed_at"),
        failed_at=task.get("failed_at"),
        result=task.get("result"),
        error=task.get("error")
    )

    return response
