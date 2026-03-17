"""
大纲数据库服务
"""
from typing import Optional, List, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.syllabus import Syllabus, Chapter, KnowledgePoint, Lexicon
from app.core.logging_config import get_logger
from app.services.embedding_service import batch_generate_embeddings

logger = get_logger(__name__)


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
    def _flatten_content(content) -> dict:
        """展平 content 列表为字典"""
        if isinstance(content, dict):
            return content
        flat = {}
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    flat.update(item)
        return flat

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
        result_data = result.get("result", {})

        # 兼容两种结构：
        # 1. result["result"] = {"keywords": [...], "usage": {...}}
        # 2. result["result"] = [...]  (直接是 keywords 列表)
        if isinstance(result_data, dict):
            keywords = result_data.get("keywords", [])
        elif isinstance(result_data, list):
            keywords = result_data
        else:
            keywords = []

        for idx, kw in enumerate(keywords):
            try:
                # 类型检查：确保 kw 是字典
                if not isinstance(kw, dict):
                    logger.warning(f"跳过第 {idx+1} 个元素，类型错误: {type(kw)}")
                    continue

                chapter_title = kw.get("chapter", "")
                # 优先使用 num 字段，不存在时从标题解析
                chapter_num = kw.get("num", 0)
                if chapter_num == 0:
                    chapter_num = SyllabusService._extract_chapter_num(chapter_title)

                chapter = Chapter(
                    syllabus_id=syllabus.id,
                    chapter_num=chapter_num,
                    chapter_title=chapter_title,
                )
                db.add(chapter)
                await db.flush()

                content = SyllabusService._flatten_content(kw.get("content", {}))
                for category in ["basic", "keypoints", "difficulty", "politics"]:
                    items = content.get(category, [])
                    if not isinstance(items, list):
                        continue

                    for item in items:
                        if not isinstance(item, dict):
                            continue

                        kp = KnowledgePoint(
                            chapter_id=chapter.id,
                            category=category,
                            title=item.get("title", ""),
                            summary=item.get("summary", ""),
                        )
                        db.add(kp)
                        await db.flush()

                        lexicon_list = item.get("lexicon", [])
                        if not isinstance(lexicon_list, list):
                            lexicon_list = []

                        # 收集有效词库项，稍后批量生成 embedding
                        valid_terms = [t for t in lexicon_list if t and isinstance(t, str)]
                        if valid_terms:
                            try:
                                embeddings = await batch_generate_embeddings(valid_terms)
                            except Exception as emb_err:
                                logger.warning(f"章节 {chapter_title} 生成 embedding 失败: {emb_err}")
                                embeddings = [None] * len(valid_terms)

                            for term, emb in zip(valid_terms, embeddings):
                                db.add(Lexicon(
                                    knowledge_point_id=kp.id,
                                    term=term,
                                    embedding=emb,
                                ))
            except Exception as e:
                import logging
                logging.error(f"处理第 {idx+1} 章节时出错: {chapter_title}, 错误: {e}")
                raise

        await db.commit()
        return syllabus.id

    @staticmethod
    def _extract_chapter_num(chapter_title: str) -> int:
        """从章节标题提取章节号"""
        import re

        match = re.search(r"第?(\d+)[章节]", chapter_title)
        return int(match.group(1)) if match else 0

    @staticmethod
    async def get_lexicons(
        db: AsyncSession,
        task_id: str,
        chapter_num: int,
        point_title: str,
        category: str,
    ) -> Optional[Dict[str, Any]]:
        """查询指定知识点的词库（分步验证）"""
        # 1. 验证task_id
        syllabus_result = await db.execute(
            select(Syllabus).where(Syllabus.task_id == task_id)
        )
        syllabus = syllabus_result.scalar_one_or_none()
        if not syllabus:
            raise ValueError(f"task_not_found:大纲任务 {task_id} 不存在")

        # 2. 验证章节
        chapter_result = await db.execute(
            select(Chapter)
            .where(Chapter.syllabus_id == syllabus.id, Chapter.chapter_num == chapter_num)
        )
        chapter = chapter_result.scalar_one_or_none()
        if not chapter:
            raise ValueError(f"chapter_not_found:章节 {chapter_num} 不存在")

        # 3. 验证知识点
        point_result = await db.execute(
            select(KnowledgePoint)
            .where(
                KnowledgePoint.chapter_id == chapter.id,
                KnowledgePoint.title == point_title,
                KnowledgePoint.category == category,
            )
            .options(selectinload(KnowledgePoint.lexicons))
        )
        point = point_result.scalar_one_or_none()
        if not point:
            raise ValueError(f"point_not_found:知识点 '{point_title}' (类别:{category}) 不存在")

        return {
            "task_id": task_id,
            "chapter_num": chapter.chapter_num,
            "chapter_title": chapter.chapter_title,
            "point_title": point.title,
            "category": point.category,
            "lexicons": [lex.term for lex in point.lexicons],
        }

    @staticmethod
    async def add_lexicons(
        db: AsyncSession,
        task_id: str,
        chapter_num: int,
        point_title: str,
        category: str,
        lexicons: List[str],
    ) -> Dict[str, Any]:
        """添加词库（去重、带验证、悲观锁）"""
        # 1. 验证task_id是否存在
        syllabus_result = await db.execute(
            select(Syllabus).where(Syllabus.task_id == task_id)
        )
        syllabus = syllabus_result.scalar_one_or_none()
        if not syllabus:
            raise ValueError(f"task_not_found:大纲任务 {task_id} 不存在")

        # 2. 验证章节是否存在
        chapter_result = await db.execute(
            select(Chapter)
            .where(Chapter.syllabus_id == syllabus.id, Chapter.chapter_num == chapter_num)
        )
        chapter = chapter_result.scalar_one_or_none()
        if not chapter:
            raise ValueError(f"chapter_not_found:章节 {chapter_num} 不存在")

        # 3. 验证知识点是否存在（使用悲观锁）
        point_result = await db.execute(
            select(KnowledgePoint)
            .where(
                KnowledgePoint.chapter_id == chapter.id,
                KnowledgePoint.title == point_title,
                KnowledgePoint.category == category,
            )
            .options(selectinload(KnowledgePoint.lexicons))
            .with_for_update()  # 悲观锁
        )
        point = point_result.scalar_one_or_none()
        if not point:
            raise ValueError(f"point_not_found:知识点 '{point_title}' (类别:{category}) 不存在")

        # 4. 检查数量限制
        current_count = len(point.lexicons)
        if current_count >= 50:
            raise ValueError(f"lexicon_limit:词库数量已达上限(50个),请先删除部分词库再添加")

        # 5. 去重并过滤
        existing = {lex.term for lex in point.lexicons}
        new_terms = [term for term in lexicons if term not in existing]

        if not new_terms:
            raise ValueError("conflict:所有词库已存在")

        # 6. 检查添加后是否超限
        if current_count + len(new_terms) > 25:
            raise ValueError(f"lexicon_limit:添加后将超过上限，当前{current_count}个，最多还能添加{25-current_count}个")

        # 7. 批量生成 embedding 并添加
        try:
            embeddings = await batch_generate_embeddings(new_terms)
        except Exception as e:
            logger.warning(f"生成 embedding 失败，词库仍会添加: {e}")
            embeddings = [None] * len(new_terms)

        new_lexicons = [
            Lexicon(knowledge_point_id=point.id, term=term, embedding=emb)
            for term, emb in zip(new_terms, embeddings)
        ]
        db.add_all(new_lexicons)

        await db.commit()
        await db.refresh(point)

        logger.info(f"添加词库成功: task_id={task_id}, chapter={chapter_num}, point={point_title}, added={len(new_terms)}")

        return {
            "task_id": task_id,
            "chapter_num": chapter.chapter_num,
            "chapter_title": chapter.chapter_title,
            "point_title": point.title,
            "category": point.category,
            "lexicons": [lex.term for lex in point.lexicons],
        }

    @staticmethod
    async def update_lexicons(
        db: AsyncSession,
        task_id: str,
        chapter_num: int,
        point_title: str,
        category: str,
        lexicons: List[str],
    ) -> Dict[str, Any]:
        """替换词库（带验证、悲观锁、事务安全）"""
        # 1. 验证task_id
        syllabus_result = await db.execute(
            select(Syllabus).where(Syllabus.task_id == task_id)
        )
        syllabus = syllabus_result.scalar_one_or_none()
        if not syllabus:
            raise ValueError(f"task_not_found:大纲任务 {task_id} 不存在")

        # 2. 验证章节
        chapter_result = await db.execute(
            select(Chapter)
            .where(Chapter.syllabus_id == syllabus.id, Chapter.chapter_num == chapter_num)
        )
        chapter = chapter_result.scalar_one_or_none()
        if not chapter:
            raise ValueError(f"chapter_not_found:章节 {chapter_num} 不存在")

        # 3. 验证知识点（悲观锁）
        point_result = await db.execute(
            select(KnowledgePoint)
            .where(
                KnowledgePoint.chapter_id == chapter.id,
                KnowledgePoint.title == point_title,
                KnowledgePoint.category == category,
            )
            .options(selectinload(KnowledgePoint.lexicons))
            .with_for_update()
        )
        point = point_result.scalar_one_or_none()
        if not point:
            raise ValueError(f"point_not_found:知识点 '{point_title}' (类别:{category}) 不存在")

        # 4. 检查数量限制
        if len(lexicons) > 25:
            raise ValueError(f"lexicon_limit:词库数量不能超过25个，当前提交{len(lexicons)}个")

        # 5. 批量生成 embedding 并构建新对象（事务安全）
        try:
            embeddings = await batch_generate_embeddings(lexicons)
        except Exception as e:
            logger.warning(f"生成 embedding 失败，词库仍会更新: {e}")
            embeddings = [None] * len(lexicons)

        new_lexicons = [
            Lexicon(knowledge_point_id=point.id, term=term, embedding=emb)
            for term, emb in zip(lexicons, embeddings)
        ]

        # 6. 删除旧词库
        for lex in point.lexicons:
            await db.delete(lex)

        # 7. 添加新词库
        db.add_all(new_lexicons)

        await db.commit()
        await db.refresh(point)

        logger.info(f"更新词库成功: task_id={task_id}, chapter={chapter_num}, point={point_title}, count={len(lexicons)}")

        return {
            "task_id": task_id,
            "chapter_num": chapter.chapter_num,
            "chapter_title": chapter.chapter_title,
            "point_title": point.title,
            "category": point.category,
            "lexicons": [lex.term for lex in point.lexicons],
        }

    @staticmethod
    async def delete_lexicons(
        db: AsyncSession,
        task_id: str,
        chapter_num: int,
        point_title: str,
        category: str,
        lexicons: List[str],
    ) -> Dict[str, Any]:
        """删除指定词库（带验证、悲观锁）"""
        # 1. 验证task_id
        syllabus_result = await db.execute(
            select(Syllabus).where(Syllabus.task_id == task_id)
        )
        syllabus = syllabus_result.scalar_one_or_none()
        if not syllabus:
            raise ValueError(f"task_not_found:大纲任务 {task_id} 不存在")

        # 2. 验证章节
        chapter_result = await db.execute(
            select(Chapter)
            .where(Chapter.syllabus_id == syllabus.id, Chapter.chapter_num == chapter_num)
        )
        chapter = chapter_result.scalar_one_or_none()
        if not chapter:
            raise ValueError(f"chapter_not_found:章节 {chapter_num} 不存在")

        # 3. 验证知识点（悲观锁）
        point_result = await db.execute(
            select(KnowledgePoint)
            .where(
                KnowledgePoint.chapter_id == chapter.id,
                KnowledgePoint.title == point_title,
                KnowledgePoint.category == category,
            )
            .options(selectinload(KnowledgePoint.lexicons))
            .with_for_update()
        )
        point = point_result.scalar_one_or_none()
        if not point:
            raise ValueError(f"point_not_found:知识点 '{point_title}' (类别:{category}) 不存在")

        # 4. 删除指定词库
        terms_to_delete = set(lexicons)
        deleted = []

        for lex in point.lexicons:
            if lex.term in terms_to_delete:
                await db.delete(lex)
                deleted.append(lex.term)

        if not deleted:
            raise ValueError(f"lexicon_not_found:指定的词库不存在")

        await db.commit()
        await db.refresh(point)

        logger.info(f"删除词库成功: task_id={task_id}, chapter={chapter_num}, point={point_title}, deleted={len(deleted)}")

        return {
            "task_id": task_id,
            "chapter_num": chapter.chapter_num,
            "chapter_title": chapter.chapter_title,
            "point_title": point.title,
            "category": point.category,
            "lexicons": [lex.term for lex in point.lexicons],
        }

    @staticmethod
    async def get_syllabus_full(
        db: AsyncSession, task_id: str
    ) -> Optional[Dict[str, Any]]:
        """从数据库重建完整大纲结构（含最新词库）"""
        syllabus = await SyllabusService.get_syllabus_by_task_id(
            db, task_id, with_relations=True
        )
        if not syllabus:
            return None

        chapters_sorted = sorted(syllabus.chapters, key=lambda c: c.chapter_num)
        result = []

        for chapter in chapters_sorted:
            content_dict = {"basic": [], "keypoints": [], "difficulty": [], "politics": []}

            for point in chapter.knowledge_points:
                item = {
                    "title": point.title,
                    "summary": point.summary,
                    "lexicon": [lex.term for lex in point.lexicons],
                }
                if point.category in content_dict:
                    content_dict[point.category].append(item)

            result.append({
                "chapter": chapter.chapter_title,
                "num": chapter.chapter_num,
                "content": [content_dict],
            })

        return {"course": syllabus.course, "result": result}
