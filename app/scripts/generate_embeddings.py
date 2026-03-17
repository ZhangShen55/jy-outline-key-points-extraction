"""
批量为现有 lexicon 生成 embedding
用法: python -m app.scripts.generate_embeddings
"""
import asyncio
from sqlalchemy import select, func

from app.core.database import AsyncSessionLocal
from app.core.config import get_settings
from app.models.syllabus import Lexicon
from app.services.embedding_service import batch_generate_embeddings


async def main():
    settings = get_settings()
    batch_size = settings.EMBEDDING_BATCH_SIZE

    async with AsyncSessionLocal() as db:
        # 统计待处理数量
        count_result = await db.execute(
            select(func.count(Lexicon.id)).where(Lexicon.embedding.is_(None))
        )
        total = count_result.scalar()
        print(f"待生成 embedding 的词库: {total} 条")

        if total == 0:
            print("无需处理")
            return

        processed = 0
        while True:
            # 查询一批未生成 embedding 的 lexicon
            result = await db.execute(
                select(Lexicon)
                .where(Lexicon.embedding.is_(None))
                .limit(batch_size)
            )
            lexicons = result.scalars().all()

            if not lexicons:
                break

            terms = [lex.term for lex in lexicons]

            try:
                embeddings = await batch_generate_embeddings(terms)

                for lex, emb in zip(lexicons, embeddings):
                    lex.embedding = emb

                await db.commit()
                processed += len(lexicons)
                print(f"进度: {processed}/{total} ({processed * 100 // total}%)")

            except Exception as e:
                print(f"批次处理失败: {e}")
                await db.rollback()
                break

    print(f"完成! 共处理 {processed} 条")


if __name__ == "__main__":
    asyncio.run(main())
