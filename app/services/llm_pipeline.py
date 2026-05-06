"""LLM 文档提取管道。"""

import asyncio
import json
import re
import time
import json_repair
from typing import List, Dict, Any, Optional

from openai import AsyncOpenAI

from app.prompts.chapter import JSON_EXTRACTION_PROMPT_TEMPLATE
from app.prompts.extractmd import MARKDOWN_EXTRACTION_PROMPT
from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class LLMPipeline:
    """LLM 文档提取管道。

    支持两种模式：
    1. VLM 模式（doubao 等）：直接将 PDF 文件传给视觉大模型提取 Markdown
    2. MinerU 模式：先用 MinerU 解析文档得到 Markdown，再用纯文本 LLM 提取结构化内容
    """

    def __init__(self):
        settings = get_settings()
        self.client = AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
        )
        self.model = settings.LLM_MODEL
        self.mineru_enabled = settings.MINERU_ENABLED

    async def run(self, filedata: str = None, orig_name: str = None,
                  markdown_content: str = None) -> dict:
        """执行完整提取流程。

        Args:
            filedata: PDF 文件的 base64 编码（VLM 模式使用）
            orig_name: 原始文件名
            markdown_content: 预先解析好的 Markdown 内容（MinerU 模式使用）
        """
        total_start = time.time()

        # 获取 Markdown 内容
        if markdown_content is not None:
            # MinerU 模式：先用 LLM 将原始 Markdown 重组为有层次的 Markdown
            logger.info("[1/4] MinerU 已解析文档，重组 Markdown 结构...")
            md_content = await self._restructure_markdown(markdown_content)
        else:
            # VLM 模式：通过视觉大模型提取 Markdown
            logger.info("[1/4] 提取文档 Markdown 结构（VLM 模式）...")
            md_content = await self._extract_markdown(filedata, orig_name)

        logger.info("[2/4] 解析 Markdown 章节结构...")
        course_name, chapters = self._parse_markdown_structure(md_content)
        logger.info(f"识别到课程: {course_name}，章节数: {len(chapters)}")

        logger.info("[3/4] 并发提取章节要点...")
        chapter_results = await self._process_chapters_concurrently(course_name, chapters)

        logger.info("[4/4] 汇总章节结果...")
        final_result = self._merge_results(course_name, chapter_results, total_start)

        total_time = time.time() - total_start
        logger.info(f"✅ 全部流程完成，总耗时: {total_time:.2f}s")

        return final_result

    async def _extract_markdown(self, filedata: str, orig_name: str = None) -> str:
        """通过视觉大模型提取文档 Markdown 结构。"""
        filename = (orig_name or "document") + ".pdf"

        response = await self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "file_data": f"data:application/pdf;base64,{filedata}",
                            "filename": filename
                        },
                        {
                            "type": "input_text",
                            "text": MARKDOWN_EXTRACTION_PROMPT
                        }
                    ]
                }
            ],
            timeout=600
        )

        return response.output[1].content[0].text

    async def _restructure_markdown(self, raw_markdown: str) -> str:
        """让纯文本 LLM 将 MinerU 原始 Markdown 重组为有层次的 Markdown。

        MinerU 输出的 Markdown 标题层级不够清晰（通常都是 # 一级标题），
        需要让 LLM 重新整理为有层次的 # / ## / ### 结构。
        """
        prompt = MARKDOWN_EXTRACTION_PROMPT

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一位文档结构分析专家，擅长将扁平的文档内容重组为层次清晰的 Markdown 结构。"},
                {"role": "user", "content": f"{prompt}\n\n【原始文档内容】\n{raw_markdown}"}
            ],
            timeout=600
        )

        content = response.choices[0].message.content

        # 如果 LLM 返回了代码块包裹的内容，提取出来
        code_block_match = re.search(r"```(?:markdown|md)?\s*\n(.*?)```", content, re.DOTALL)
        if code_block_match:
            content = code_block_match.group(1).strip()

        return content

    def _parse_markdown_structure(self, markdown_content: str) -> tuple:
        """按 Markdown 标题切分课程与章节。"""
        lines = markdown_content.strip().split('\n')

        course_name = None
        chapters = []
        current_chapter = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith('# ') and not line.startswith('##'):
                course_name = line[2:].strip()

            elif line.startswith('## ') and not line.startswith('###'):
                if current_chapter:
                    chapters.append(current_chapter)
                current_chapter = {
                    'title': line[3:].strip(),
                    'content': []
                }

            elif current_chapter and (line.startswith('#') or line):
                current_chapter['content'].append(line)

        if current_chapter:
            chapters.append(current_chapter)

        return course_name, chapters

    async def _process_chapters_concurrently(self, course_name: str, chapters: List[dict]) -> List[dict]:
        """并发提取各章节的结构化内容。"""
        tasks = [
            self._process_single_chapter(course_name, chapter)
            for chapter in chapters
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        successful_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"章节 {i} 处理失败: {result}")
            else:
                successful_results.append(result)

        return successful_results

    async def _process_single_chapter(self, course_name: str, chapter: dict) -> dict:
        """处理单个章节"""
        chapter_title = chapter['title']
        chapter_content = '\n'.join(chapter['content'])

        prompt = JSON_EXTRACTION_PROMPT_TEMPLATE.format(课程名=course_name)

        input_content = f"## {chapter_title}\n{chapter_content}"

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一位课程大纲分析专家，擅长从教学大纲中提取结构化信息。"},
                {"role": "user", "content": f"{prompt}\n\n【输入内容】\n{input_content}"}
            ],
            timeout=300
        )

        result_json = json.loads(json_repair.repair_json(response.choices[0].message.content))

        return {
            'chapter_title': chapter_title,
            'result': result_json,
            'usage': {
                'prompt_tokens': response.usage.prompt_tokens,
                'completion_tokens': response.usage.completion_tokens,
                'total_tokens': response.usage.total_tokens
            }
        }

    def _merge_results(self, course_name: str, chapter_results: List[dict], start_time: float) -> dict:
        """合并章节结果并进行全局词库去重。"""
        total_prompt_tokens = 0
        total_completion_tokens = 0

        keywords = []

        seen_lexicons = set()

        for result in chapter_results:
            chapter_data = result['result']
            usage = result['usage']

            total_prompt_tokens += usage['prompt_tokens']
            total_completion_tokens += usage['completion_tokens']

            if 'content' in chapter_data:
                for module in chapter_data['content']:
                    for module_name, items in module.items():
                        if isinstance(items, list):
                            for item in items:
                                if 'lexicon' in item and isinstance(item['lexicon'], list):
                                    unique_lexicons = []
                                    for lex in item['lexicon']:
                                        if lex not in seen_lexicons:
                                            seen_lexicons.add(lex)
                                            unique_lexicons.append(lex)
                                    import random
                                    random.shuffle(unique_lexicons)
                                    item['lexicon'] = unique_lexicons

            keywords.append(chapter_data)

        final_result = {
            'course': course_name,
            'result': keywords,
            'usage': {
                'prompt_tokens': total_prompt_tokens,
                'completion_tokens': total_completion_tokens,
                'total_tokens': total_prompt_tokens + total_completion_tokens
            }
        }

        return final_result


# 便捷调用入口

async def run_llm_pipeline(filedata: str = None, orig_name: str = None,
                           markdown_content: str = None) -> dict:
    """运行 LLM 提取管道。

    Args:
        filedata: PDF 文件的 base64 编码（VLM 模式）
        orig_name: 原始文件名
        markdown_content: 预解析的 Markdown 内容（MinerU 模式）
    """
    pipeline = LLMPipeline()
    return await pipeline.run(filedata=filedata, orig_name=orig_name,
                              markdown_content=markdown_content)
