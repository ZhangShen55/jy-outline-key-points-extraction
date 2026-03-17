"""
词库语义匹配服务
Embedding 向量召回 + Rerank 精排
"""
from typing import Optional, List, Dict, Any
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.syllabus import Syllabus, Chapter, KnowledgePoint, Lexicon
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.validators import VALID_CATEGORIES
from app.services.embedding_service import generate_embedding
from app.services import rerank_service

logger = get_logger(__name__)


async def _validate_scope(
    db: AsyncSession,
    task_id: Optional[str],
    chapter_num: Optional[int],
    category: Optional[str],
    point_title: Optional[str],
) -> Dict[str, Any]:
    """验证层级参数依赖关系，返回搜索范围"""
    scope = {}
    syllabus = None
    chapter = None

    if point_title and not category:
        raise ValueError("point_title 需要同时提供 category")
    if category and chapter_num is None:
        raise ValueError("category 需要同时提供 chapter_num")
    if chapter_num is not None and not task_id:
        raise ValueError("chapter_num 需要同时提供 task_id")

    if task_id:
        result = await db.execute(
            select(Syllabus).where(Syllabus.task_id == task_id)
        )
        syllabus = result.scalar_one_or_none()
        if not syllabus:
            raise ValueError(f"大纲任务 {task_id} 不存在")
        scope["task_id"] = task_id

    if chapter_num is not None and syllabus:
        result = await db.execute(
            select(Chapter).where(
                Chapter.syllabus_id == syllabus.id,
                Chapter.chapter_num == chapter_num,
            )
        )
        chapter = result.scalar_one_or_none()
        if not chapter:
            raise ValueError(f"章节 {chapter_num} 不存在")
        scope["chapter_num"] = chapter_num

    if category:
        if category not in VALID_CATEGORIES:
            raise ValueError(f"类别必须是: {', '.join(VALID_CATEGORIES)}")
        scope["category"] = category

    if point_title and chapter:
        result = await db.execute(
            select(KnowledgePoint).where(
                KnowledgePoint.chapter_id == chapter.id,
                KnowledgePoint.title == point_title,
                KnowledgePoint.category == category,
            )
        )
        point = result.scalar_one_or_none()
        if not point:
            raise ValueError(f"知识点 '{point_title}' 不存在")
        scope["point_title"] = point_title

    return scope


def _build_where_clause(
    task_id: Optional[str],
    chapter_num: Optional[int],
    category: Optional[str],
    point_title: Optional[str],
) -> str:
    """构建 SQL WHERE 子句"""
    conditions = ["l.embedding IS NOT NULL"]
    if task_id:
        conditions.append("s.task_id = :task_id")
    if chapter_num is not None:
        conditions.append("c.chapter_num = :chapter_num")
    if category:
        conditions.append("kp.category = :category")
    if point_title:
        conditions.append("kp.title = :point_title")
    return " AND ".join(conditions)


async def match_lexicons(
    db: AsyncSession,
    query_text: str,
    task_id: Optional[str] = None,
    chapter_num: Optional[int] = None,
    category: Optional[str] = None,
    point_title: Optional[str] = None,
    top: int = 1,
    min_score: Optional[float] = None,
) -> Dict[str, Any]:
    """词库语义匹配：向量召回 + 动态 rerank"""
    settings = get_settings()
    if min_score is None:
        min_score = settings.MATCH_DEFAULT_MIN_SCORE

    # 1. 验证搜索范围
    scope = await _validate_scope(db, task_id, chapter_num, category, point_title)

    # 2. 生成查询向量
    query_embedding = await generate_embedding(query_text)
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    # 3. pgvector 向量搜索
    where_clause = _build_where_clause(task_id, chapter_num, category, point_title)
    recall_limit = max(top * 10, 100) if settings.RERANK_ENABLED else top

    sql = f"""
        SELECT
            l.id, l.term,
            1 - (l.embedding <=> :embedding) AS cosine_score,
            kp.title AS point_title, kp.category,
            c.chapter_num, c.chapter_title,
            s.task_id, s.course
        FROM lexicons l
        JOIN knowledge_points kp ON l.knowledge_point_id = kp.id
        JOIN chapters c ON kp.chapter_id = c.id
        JOIN syllabuses s ON c.syllabus_id = s.id
        WHERE {where_clause}
        ORDER BY l.embedding <=> :embedding
        LIMIT :recall_limit
    """

    params = {"embedding": embedding_str, "recall_limit": recall_limit}
    if task_id:
        params["task_id"] = task_id
    if chapter_num is not None:
        params["chapter_num"] = chapter_num
    if category:
        params["category"] = category
    if point_title:
        params["point_title"] = point_title

    result = await db.execute(text(sql), params)
    candidates = result.fetchall() # 全部结果

    if not candidates:
        return _empty_response(query_text, top, scope)

    # 4. 动态 rerank
    use_rerank = (
        settings.RERANK_ENABLED and len(candidates) > settings.RERANK_THRESHOLD
    )

    if use_rerank:
        scored = await _do_rerank(query_text, candidates, top)
    else:
        scored = [
            _candidate_to_dict(c, round(float(c.cosine_score), 6))
            for c in candidates[:top]
        ]

    # 5. 过滤低于阈值
    filtered = [s for s in scored if s["score"] >= min_score][:top]

    # 6. 构建响应
    results = [
        {
            "course": item["course"],
            "lexicon": item["term"],
            "score": round(item["score"], 6),
            "source": {
                "task_id": item["task_id"],
                "chapter_num": item["chapter_num"],
                "chapter_title": item["chapter_title"],
                "category": item["category"],
                "point_title": item["point_title"],
            },
        }
        for item in filtered
    ]

    if results:
        status_code = 200
        message = f"匹配到 {len(results)} 个词库"
    else:
        status_code = 404
        message = f"未找到相似度高于 {min_score} 的匹配结果"

    return {
        "text": query_text,
        "top": top,
        "search_scope": scope,
        "status_code": status_code,
        "message": message,
        "results": results,
    }


async def _do_rerank(query_text, candidates, top):
    """Rerank 精排"""
    documents = [c.term for c in candidates]
    rerank_results = await rerank_service.rerank(
        query=query_text, documents=documents, top_n=top,
    )
    scored = []
    for rr in rerank_results:
        c = candidates[rr["index"]]
        scored.append(_candidate_to_dict(c, rr["relevance_score"]))
    logger.info(f"Rerank: {len(candidates)} 候选 → {len(scored)} 结果")
    return scored


def _candidate_to_dict(c, score):
    return {
        "score": score,
        "term": c.term,
        "course": c.course,
        "task_id": c.task_id,
        "chapter_num": c.chapter_num,
        "chapter_title": c.chapter_title,
        "category": c.category,
        "point_title": c.point_title,
    }


def _empty_response(query_text, top, scope):
    return {
        "text": query_text,
        "top": top,
        "search_scope": scope,
        "status_code": 404,
        "message": "搜索范围内无可用词库（可能尚未生成 embedding）",
        "results": [],
    }