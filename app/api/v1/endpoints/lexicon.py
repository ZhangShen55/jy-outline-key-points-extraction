"""
词库管理端点
"""
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging_config import get_logger
from app.core.validators import validate_category, validate_lexicons
from app.schemas.request import LexiconRequest, LexiconMatchRequest
from app.schemas.response import LexiconResponse, LexiconMatchResponse
from app.services.db.syllabus_service import SyllabusService
from app.services import lexicon_match_service

logger = get_logger(__name__)
router = APIRouter()


def parse_error(error_msg: str) -> tuple[int, str]:
    """解析错误消息，返回状态码和消息"""
    if ":" in error_msg:
        error_type, msg = error_msg.split(":", 1)
        if error_type == "task_not_found":
            return 404, msg
        elif error_type == "chapter_not_found":
            return 404, msg
        elif error_type == "point_not_found":
            return 404, msg
        elif error_type == "lexicon_not_found":
            return 404, msg
        elif error_type == "conflict":
            return 409, msg
        elif error_type == "lexicon_limit":
            return 409, msg
    return 400, error_msg


# 具体路径必须在通配符路径之前
@router.get("/lexicon", response_model=LexiconResponse)
async def get_lexicons(
    task_id: str = Query(..., description="大纲任务ID"),
    chapter_num: int = Query(..., description="章节号"),
    point_title: str = Query(..., description="知识点标题"),
    category: str = Query(..., description="类别: basic/keypoints/difficulty/politics"),
    db: AsyncSession = Depends(get_db),
):
    """查询词库"""
    # 验证category
    valid, msg = validate_category(category)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    try:
        result = await SyllabusService.get_lexicons(
            db, task_id, chapter_num, point_title, category
        )
        return LexiconResponse(**result)
    except ValueError as e:
        status_code, detail = parse_error(str(e))
        raise HTTPException(status_code=status_code, detail=detail)
    except Exception as e:
        logger.error(f"查询词库失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.get("/{task_id}", response_model=Dict[str, Any])
async def get_syllabus_full(
    task_id: str = Path(..., description="大纲任务ID"),
    db: AsyncSession = Depends(get_db),
):
    """获取完整大纲结构（含最新词库修改）"""
    result = await SyllabusService.get_syllabus_full(db, task_id)
    if not result:
        raise HTTPException(status_code=404, detail="大纲不存在")
    return result


@router.post("/lexicon", response_model=LexiconResponse, status_code=201)
async def add_lexicons(
    request: LexiconRequest,
    db: AsyncSession = Depends(get_db),
):
    """添加词库（支持批量，自动去重）"""
    # 1. 验证category
    valid, msg = validate_category(request.category)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    # 2. 验证lexicons
    valid, msg, cleaned_lexicons = validate_lexicons(request.lexicons)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    try:
        result = await SyllabusService.add_lexicons(
            db,
            request.task_id,
            request.chapter_num,
            request.point_title,
            request.category,
            cleaned_lexicons,
        )
        logger.info(f"添加词库: {request.task_id}/{request.chapter_num}/{request.point_title}")
        return LexiconResponse(**result)
    except ValueError as e:
        status_code, detail = parse_error(str(e))
        raise HTTPException(status_code=status_code, detail=detail)
    except Exception as e:
        logger.error(f"添加词库失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.put("/lexicon", response_model=LexiconResponse)
async def update_lexicons(
    request: LexiconRequest,
    db: AsyncSession = Depends(get_db),
):
    """更新词库（完全替换）"""
    # 1. 验证category
    valid, msg = validate_category(request.category)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    # 2. 验证lexicons
    valid, msg, cleaned_lexicons = validate_lexicons(request.lexicons)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    try:
        result = await SyllabusService.update_lexicons(
            db,
            request.task_id,
            request.chapter_num,
            request.point_title,
            request.category,
            cleaned_lexicons,
        )
        logger.info(f"更新词库: {request.task_id}/{request.chapter_num}/{request.point_title}")
        return LexiconResponse(**result)
    except ValueError as e:
        status_code, detail = parse_error(str(e))
        raise HTTPException(status_code=status_code, detail=detail)
    except Exception as e:
        logger.error(f"更新词库失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.delete("/lexicon", response_model=LexiconResponse)
async def delete_lexicons(
    task_id: str = Query(..., description="大纲任务ID"),
    chapter_num: int = Query(..., description="章节号"),
    point_title: str = Query(..., description="知识点标题"),
    category: str = Query(..., description="类别"),
    terms: str = Query(..., description="要删除的词库，多个用逗号分隔"),
    db: AsyncSession = Depends(get_db),
):
    """删除词库（支持批量，使用query参数）"""
    # 1. 验证category
    valid, msg = validate_category(category)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    # 2. 解析terms
    lexicons = [term.strip() for term in terms.split(",") if term.strip()]
    if not lexicons:
        raise HTTPException(status_code=400, detail="terms参数不能为空")

    try:
        result = await SyllabusService.delete_lexicons(
            db, task_id, chapter_num, point_title, category, lexicons
        )
        logger.info(f"删除词库: {task_id}/{chapter_num}/{point_title}, deleted={len(lexicons)}")
        return LexiconResponse(**result)
    except ValueError as e:
        status_code, detail = parse_error(str(e))
        raise HTTPException(status_code=status_code, detail=detail)
    except Exception as e:
        logger.error(f"删除词库失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.post("/match", response_model=LexiconMatchResponse)
async def match_lexicons(
    request: LexiconMatchRequest,
    db: AsyncSession = Depends(get_db),
):
    """词库语义匹配（Embedding + Rerank）"""
    try:
        result = await lexicon_match_service.match_lexicons(
            db=db,
            query_text=request.text,
            task_id=request.task_id,
            chapter_num=request.chapter_num,
            category=request.category,
            point_title=request.point_title,
            top=request.top,
            min_score=request.min_score,
        )
        return LexiconMatchResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"词库匹配失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="服务器内部错误")
