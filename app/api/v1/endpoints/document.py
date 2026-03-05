"""
文档处理端点
"""
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pathlib import Path
import tempfile
import uuid
import base64
from datetime import datetime
from typing import Dict

from app.schemas.request import ProcessRequest
from app.schemas.response import TaskResponse, TaskStatusResponse
from app.services.pipeline import run_pipeline
from app.core.logging_config import get_logger
from app.core.exceptions import BadRequestException, NotFoundException

logger = get_logger(__name__)
router = APIRouter()

# 任务存储（生产环境应使用 Redis 或数据库）
tasks: Dict[str, dict] = {}


async def process_document_background(task_id: str, tmp_path: Path, orig_name: str):
    """后台处理文档的异步函数"""
    try:
        # 更新任务状态为处理中
        tasks[task_id]["status"] = "processing"
        tasks[task_id]["message"] = "文档处理中..."
        tasks[task_id]["started_at"] = datetime.now().isoformat()

        logger.info(f"🔄 开始处理任务 {task_id}")

        # 执行处理
        result = await run_pipeline(tmp_path, orig_name)
        result["id"] = task_id

        # 更新任务状态为完成
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

    finally:
        # 清理临时文件
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()


@router.post("/process", response_model=TaskResponse, status_code=202)
async def process_document(request: ProcessRequest, background_tasks: BackgroundTasks):
    """
    提交文档处理任务（异步）

    立即返回任务ID，不等待处理完成。
    使用 GET /document/status/{task_id} 查询处理进度。
    """
    try:
        task_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

        # 将 Base64 转回文件
        file_bytes = base64.b64decode(request.filedata)
        suffix = Path(request.filename).suffix

        # 保存临时文件
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = Path(tmp.name)

        # 取客户端的原始文件名（不带后缀）
        orig_name = Path(request.filename).stem

        # 初始化任务状态
        tasks[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "filename": request.filename,
            "message": "任务已提交，等待处理...",
            "result": None,
            "error": None,
            "created_at": datetime.now().isoformat()
        }

        # 添加到后台任务
        background_tasks.add_task(process_document_background, task_id, tmp_path, orig_name)

        logger.info(f"📝 任务 {task_id} 已提交，文件: {request.filename}")

        return TaskResponse(
            task_id=task_id,
            status="pending",
            message="任务已提交，请使用 GET /api/v1/document/status/{task_id} 查询处理进度"
        )

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
