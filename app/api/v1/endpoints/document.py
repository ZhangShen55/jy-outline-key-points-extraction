"""
文档处理端点
"""
from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File, Depends
from pathlib import Path
import asyncio
import tempfile
import uuid
import base64
import shutil
from datetime import datetime
from typing import Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.response import TaskResponse, TaskStatusResponse
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.exceptions import NotFoundException
from app.core.database import get_db
from app.core.constants import TaskStatus, TaskType
from app.services.db.task_service import TaskService

logger = get_logger(__name__)
router = APIRouter()

# 任务存储（单实例内存，多实例部署需替换为 Redis）
tasks: Dict[str, dict] = {}

# 并发控制
_settings = get_settings()
_semaphore = asyncio.Semaphore(_settings.MAX_CONCURRENT)
_queue_count = 0

# 文件类型限制
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


def validate_file(file: UploadFile) -> None:
    """验证文件类型和大小"""
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"不支持的文件类型: {ext}，仅支持 PDF、Word 文档")

    if file.size and file.size > MAX_FILE_SIZE:
        raise HTTPException(400, f"文件过大: {file.size / 1024 / 1024:.1f}MB，最大支持 50MB")


async def convert_to_pdf_base64(file: UploadFile) -> str:
    """
    将上传的文件转为 PDF base64：
    - PDF：直接编码
    - doc/docx：转换为 PDF 后编码
    """
    file_bytes = await file.read()
    ext = Path(file.filename).suffix.lower()

    if ext == ".pdf":
        return base64.b64encode(file_bytes).decode("utf-8")

    # doc/docx 转 PDF
    tmp_dir = Path(tempfile.mkdtemp())
    tmp_doc = tmp_dir / f"input{ext}"
    try:
        tmp_doc.write_bytes(file_bytes)

        from app.services.converters.office_to_pdf import convert_office_to_pdf

        pdf_path = convert_office_to_pdf(str(tmp_doc), output_dir=str(tmp_dir))
        pdf_base64 = base64.b64encode(pdf_path.read_bytes()).decode("utf-8")
        return pdf_base64
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def process_document_background(task_id: str, tmp_file: str, orig_name: str, db: AsyncSession):
    """后台处理文档的异步函数（LLM Pipeline）"""
    global _queue_count
    settings = get_settings()
    start_time = datetime.utcnow()

    # 队列已满，拒绝任务
    if _queue_count >= settings.MAX_QUEUE:
        await TaskService.fail_task(db, task_id, "服务繁忙，队列已满，请稍后重试")
        tasks[task_id]["status"] = TaskStatus.to_str(TaskStatus.FAILED)
        tasks[task_id]["error"] = "队列已满"
        logger.warning(f"⚠️ 任务 {task_id} 被拒绝：队列已满")
        return

    _queue_count += 1
    await TaskService.update_task_status(db, task_id, TaskStatus.QUEUED)
    tasks[task_id]["status"] = TaskStatus.to_str(TaskStatus.QUEUED)
    logger.info(f"📋 任务 {task_id} 进入队列，当前排队数: {_queue_count}")

    try:
        async with _semaphore:
            _queue_count -= 1
            await TaskService.update_task_status(db, task_id, TaskStatus.PROCESSING)
            tasks[task_id]["status"] = TaskStatus.PROCESSING
            tasks[task_id]["message"] = "处理中..."
            tasks[task_id]["started_at"] = datetime.utcnow().isoformat()
            logger.info(f"🔄 开始处理任务 {task_id}")

            filedata = Path(tmp_file).read_text(encoding="utf-8")

            from app.services.llm_pipeline import run_llm_pipeline

            result = await run_llm_pipeline(filedata, orig_name)
            
            # logger.info(f"✅ 任务结果result： {result} 处理完成")

            result["id"] = task_id

            elapsed = (datetime.utcnow() - start_time).total_seconds()

            # 完成任务并入库
            await TaskService.complete_task(db, task_id, result, elapsed)

            # 保存大纲结构
            from app.services.db.syllabus_service import SyllabusService

            await SyllabusService.save_full_syllabus(
                db,
                task_id=task_id,
                course=result.get("course", ""),
                filename=orig_name,
                result=result,
            )

            tasks[task_id]["status"] = TaskStatus.COMPLETED
            tasks[task_id]["result"] = result
            tasks[task_id]["completed_at"] = datetime.utcnow().isoformat()
            logger.info(f"✅ 任务 {task_id} 处理完成")

    except Exception as e:
        _queue_count = max(0, _queue_count - 1)
        import traceback
        error_detail = traceback.format_exc()
        logger.error(f"❌ 任务 {task_id} 处理失败: {e}\n完整堆栈:\n{error_detail}")
        await TaskService.fail_task(db, task_id, str(e))
        tasks[task_id]["status"] = TaskStatus.FAILED
        tasks[task_id]["error"] = str(e)
    finally:
        try:
            Path(tmp_file).unlink(missing_ok=True)
        except Exception:
            pass


@router.post("/process", response_model=TaskResponse, status_code=202)
async def process_document(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    db: AsyncSession = Depends(get_db),
):
    """
    提交文档处理任务（异步）

    支持 PDF、Word 文档上传，立即返回任务ID。
    使用 GET /document/status/{task_id} 查询处理进度。
    """
    try:
        # 1. 验证文件
        validate_file(file)

        # 2. 生成任务ID
        task_id = f"syllabus-{uuid.uuid4().hex[:24]}"
        orig_name = Path(file.filename).stem

        # 3. 转换为 PDF base64
        pdf_filedata = await convert_to_pdf_base64(file)

        # 4. base64 落盘
        tmp_fd, tmp_file = tempfile.mkstemp(suffix=".b64", prefix=f"task_{task_id}_")
        import os

        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(pdf_filedata)

        # 5. 创建数据库任务记录
        await TaskService.create_task(
            db,
            task_id=task_id,
            task_type=TaskType.SYLLABUS,
            filename=file.filename,
            file_size=file.size,
        )

        # 6. 初始化内存任务
        tasks[task_id] = {
            "task_id": task_id,
            "status": TaskStatus.PENDING,
            "message": "任务已提交，等待处理...",
            "created_at": datetime.utcnow().isoformat(),
        }

        # 7. 提交后台任务
        background_tasks.add_task(process_document_background, task_id, tmp_file, orig_name, db)

        logger.info(f"📝 任务 {task_id} 已提交，文件: {file.filename}")

        return TaskResponse(
            task_id=task_id,
            status="pending",
            message="任务已提交，请使用 GET /api/v1/document/status/{task_id} 查询处理进度",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 提交任务失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str, db: AsyncSession = Depends(get_db)):
    """
    查询大纲提取任务状态

    只查询 /process 接口提交的任务，队列信息也只显示大纲提取任务
    """
    # 1. 先查数据库
    db_task = await TaskService.get_task_by_id(db, task_id)
    if db_task:
        # 验证任务类型
        if db_task.task_type != TaskType.SYLLABUS:
            raise NotFoundException(message=f"任务 {task_id} 不是大纲提取任务")

        # 获取大纲提取任务的队列统计
        queue_stats = await TaskService.get_queue_stats(db, TaskType.SYLLABUS)

        return TaskStatusResponse(
            task_id=db_task.task_id,
            status=db_task.status,
            message=_build_message(db_task.status),
            created_at=db_task.created_at.isoformat(),
            started_at=db_task.started_at.isoformat() if db_task.started_at else None,
            completed_at=db_task.completed_at.isoformat() if db_task.completed_at else None,
            elapsed_time=db_task.elapsed_time,
            queued=queue_stats["queued"],
            processing=queue_stats["processing"],
            filename=db_task.filename,
            result=db_task.result,
            error=db_task.error,
        )

    # 2. 数据库没有，查内存
    if task_id in tasks:
        task = tasks[task_id]

        # 验证任务类型（通过task_id前缀判断）
        if not task_id.startswith("syllabus-"):
            raise NotFoundException(message=f"任务 {task_id} 不是大纲提取任务")

        queue_stats = await TaskService.get_queue_stats(db, TaskType.SYLLABUS)

        return TaskStatusResponse(
            task_id=task_id,
            status=task.get("status", TaskStatus.PENDING),
            message=task.get("message", ""),
            created_at=task.get("created_at", ""),
            started_at=task.get("started_at"),
            completed_at=task.get("completed_at"),
            elapsed_time=None,
            queued=queue_stats["queued"],
            processing=queue_stats["processing"],
            filename=task.get("filename"),
            result=task.get("result"),
            error=task.get("error"),
        )

    raise NotFoundException(message=f"任务 {task_id} 不存在")


def _build_message(status: int) -> str:
    """根据状态码构建消息"""
    if status == TaskStatus.COMPLETED:
        return "处理完成"
    if status == TaskStatus.FAILED:
        return "处理失败"
    if status == TaskStatus.QUEUED:
        return "排队等待中..."
    if status == TaskStatus.PROCESSING:
        return "处理中..."
    return "任务已提交"
