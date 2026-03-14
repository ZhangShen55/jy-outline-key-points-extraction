"""
词库管理端点
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging_config import get_logger
from app.schemas.request import LexiconRequest
from app.schemas.response import LexiconResponse
from app.services.db.syllabus_service import SyllabusService

logger = get_logger(__name__)
router = APIRouter()


@router.get("/lexicon", response_model=LexiconResponse)
async def get_lexicons(
    task_id: str = Query(..., description="大纲任务ID"),
    chapter_num: int = Query(..., description="章节号"),
    point_title: str = Query(..., description="知识点标题"),
    category: str = Query(..., description="类别: basic/keypoints/difficulty/politics"),
    db: AsyncSession = Depends(get_db),
):
    """查询词库"""
    try:
        result = await SyllabusService.get_lexicons(
            db, task_id, chapter_num, point_title, category
        )
        if not result:
            raise HTTPException(status_code=404, detail="知识点不存在")
        return LexiconResponse(**result)
    except Exception as e:
        logger.error(f"查询词库失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/lexicon", response_model=LexiconResponse, status_code=201)
async def add_lexicons(
    request: LexiconRequest,
    db: AsyncSession = Depends(get_db),
):
    """添加词库（支持批量，自动去重）"""
    try:
        result = await SyllabusService.add_lexicons(
            db,
            request.task_id,
            request.chapter_num,
            request.point_title,
            request.category,
            request.lexicons,
        )
        return LexiconResponse(**result)
    except ValueError as e:
        if "不存在" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        elif "已存在" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"添加词库失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/lexicon", response_model=LexiconResponse)
async def update_lexicons(
    request: LexiconRequest,
    db: AsyncSession = Depends(get_db),
):
    """更新词库（完全替换）"""
    try:
        result = await SyllabusService.update_lexicons(
            db,
            request.task_id,
            request.chapter_num,
            request.point_title,
            request.category,
            request.lexicons,
        )
        return LexiconResponse(**result)
    except ValueError as e:
        if "不存在" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"更新词库失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/lexicon", response_model=LexiconResponse)
async def delete_lexicons(
    request: LexiconRequest,
    db: AsyncSession = Depends(get_db),
):
    """删除词库（支持批量）"""
    try:
        result = await SyllabusService.delete_lexicons(
            db,
            request.task_id,
            request.chapter_num,
            request.point_title,
            request.category,
            request.lexicons,
        )
        return LexiconResponse(**result)
    except ValueError as e:
        if "不存在" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"删除词库失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
