"""
验证数据库表是否创建成功
"""
import asyncio
from sqlalchemy import text
from app.core.database import async_engine


async def check_tables():
    async with async_engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name
                """
            )
        )
        tables = [row[0] for row in result]

        print("数据库中的表：")
        for table in tables:
            print(f"  - {table}")

        if not tables:
            print("  (无表)")

        return tables


if __name__ == "__main__":
    tables = asyncio.run(check_tables())

    expected = ["tasks", "syllabuses", "chapters", "knowledge_points", "lexicons"]
    missing = [t for t in expected if t not in tables]

    if missing:
        print(f"\n❌ 缺少表: {missing}")
    else:
        print("\n✅ 所有表已创建")
