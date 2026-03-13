"""
大纲数据库服务
"""
from typing import Optional, List, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.syllabus import Syllabus, Chapter, KnowledgePoint, Lexicon


class SyllabusService:
    """大纲 CRUD 服务"""

    @staticmethod
    async def create_syllabus(
        db: AsyncSession,
        task_id: str,
        course: str,
        filename: str,
        raw_result: Dict[str, Any],
    ) -> Syllabus:
        """创建大纲记录"""
        syllabus = Syllabus(
            task_id=task_id,
            course=course,
            filename=filename,
            raw_result=raw_result,
        )
        db.add(syllabus)
        await db.commit()
        await db.refresh(syllabus)
        return syllabus

    @staticmethod
    async def get_syllabus_by_task_id(
        db: AsyncSession, task_id: str, with_relations: bool = False
    ) -> Optional[Syllabus]:
        """根据 task_id 查询大纲"""
        query = select(Syllabus).where(Syllabus.task_id == task_id)

        if with_relations:
            query = query.options(
                selectinload(Syllabus.chapters)
                .selectinload(Chapter.knowledge_points)
                .selectinload(KnowledgePoint.lexicons)
            )

        result = await db.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def save_full_syllabus(
        db: AsyncSession,
        task_id: str,
        course: str,
        filename: str,
        result: Dict[str, Any],
    ) -> int:
        """保存完整大纲结构（包括章节、知识点、词库）"""
        # 1. 创建大纲主记录
        syllabus = await SyllabusService.create_syllabus(
            db, task_id, course, filename, result
        )

        # 2. 解析并保存章节、知识点、词库
        keywords = result.get("result", {}).get("keywords", [])
        for kw in keywords:
            chapter_title = kw.get("chapter", "")
            chapter_num = SyllabusService._extract_chapter_num(chapter_title)

            chapter = Chapter(
                syllabus_id=syllabus.id,
                chapter_num=chapter_num,
                chapter_title=chapter_title,
            )
            db.add(chapter)
            await db.flush()

            content = kw.get("content", {})
            for category in ["basic", "keypoints", "difficulty", "politics"]:
                items = content.get(category, [])
                for item in items:
                    kp = KnowledgePoint(
                        chapter_id=chapter.id,
                        category=category,
                        title=item.get("title", ""),
                        summary=item.get("summary", ""),
                    )
                    db.add(kp)
                    await db.flush()

                    lexicon_list = item.get("lexicon", [])
                    for term in lexicon_list:
                        lexicon = Lexicon(
                            knowledge_point_id=kp.id,
                            term=term,
                            embedding=None,  # 向量后续异步生成
                        )
                        db.add(lexicon)

        await db.commit()
        return syllabus.id

    @staticmethod
    def _extract_chapter_num(chapter_title: str) -> int:
        """从章节标题提取章节号"""
        import re

        match = re.search(r"第?(\d+)[章节]", chapter_title)
        return int(match.group(1)) if match else 0
