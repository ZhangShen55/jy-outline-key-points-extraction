"""
Embedding 向量生成服务
使用 OpenAI client 对接 SiliconFlow API
"""
from typing import List
from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_client: AsyncOpenAI = None


def _get_client() -> AsyncOpenAI:
    """获取 OpenAI 异步客户端（单例）"""
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncOpenAI(
            api_key=settings.EMBEDDING_API_KEY,
            base_url=settings.EMBEDDING_BASE_URL,
        )
    return _client


async def generate_embedding(text: str) -> List[float]:
    """生成单条文本的 embedding 向量"""
    settings = get_settings()
    client = _get_client()

    response = await client.embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=text,
        dimensions=settings.EMBEDDING_DIMENSION,
    )
    return response.data[0].embedding


async def batch_generate_embeddings(texts: List[str]) -> List[List[float]]:
    """
    批量生成 embedding 向量
    SiliconFlow 支持 input 为数组，一次请求生成多条
    """
    if not texts:
        return []

    settings = get_settings()
    client = _get_client()
    batch_size = settings.EMBEDDING_BATCH_SIZE
    all_embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        response = await client.embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=batch,
            dimensions=settings.EMBEDDING_DIMENSION,
        )
        # 按 index 排序确保顺序一致
        sorted_data = sorted(response.data, key=lambda x: x.index)
        all_embeddings.extend([item.embedding for item in sorted_data])
        logger.info(f"Embedding 批次 {i // batch_size + 1}: {len(batch)} 条")

    return all_embeddings
