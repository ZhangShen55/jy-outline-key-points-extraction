"""
Rerank 重排序服务
使用 httpx 对接 SiliconFlow Rerank API（非 OpenAI 标准接口）
"""
from typing import List, Dict
import httpx

from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


async def rerank(
    query: str,
    documents: List[str],
    top_n: int = 10,
) -> List[Dict]:
    """
    对候选文档进行重排序
    返回: [{"index": 0, "relevance_score": 0.95}, ...]
    """
    settings = get_settings()

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{settings.RERANK_BASE_URL}/rerank",
            json={
                "model": settings.RERANK_MODEL,
                "query": query,
                "documents": documents,
                "top_n": top_n,
            },
            headers={
                "Authorization": f"Bearer {settings.RERANK_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        result = response.json()

    results = result.get("results", [])
    logger.info(f"Rerank 完成: query={query[:20]}..., candidates={len(documents)}, top_n={top_n}")
    return results
