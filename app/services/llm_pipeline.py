"""
LLM直接提取Pipeline（替代Dolphin方案）

流程：
1. LLM提取Markdown（Prompt 1）
2. 内存中切分（按# ##切分）
3. 多路并发处理各章节（Prompt 2）
4. 合并结果
"""

import asyncio
import json
import re
import time
from typing import List, Dict, Any

from openai import AsyncOpenAI

from app.prompts.chapter import JSON_EXTRACTION_PROMPT_TEMPLATE
from app.prompts.extractmd import MARKDOWN_EXTRACTION_PROMPT
from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


# ============ 核心类实现 ============

class LLMPipeline:
    """LLM直接提取Pipeline"""
    
    def __init__(self):
        settings = get_settings()
        self.client = AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
        )
        self.model = settings.LLM_MODEL
        
    async def run(self, filedata: str, orig_name: str = None) -> dict:
        """
        运行完整Pipeline

        Args:
            filedata: PDF 文件的 base64 字符串
            orig_name: 原始文件名（不含后缀，可选）

        Returns:
            处理结果字典
        """
        total_start = time.time()

        # Step 1: LLM提取Markdown
        logger.info("[Step 1] LLM提取Markdown...")
        markdown_content = await self._extract_markdown(filedata, orig_name)
        # print(f"提取到的Markdown内容: {markdown_content}")
        
        # Step 2: 内存中切分
        logger.info("[Step 2] 切分章节...")
        course_name, chapters = self._parse_markdown_structure(markdown_content)
        # print(f"课程名: {course_name}, 共 {len(chapters)} 个章节")
        logger.info(f"课程名: {course_name}, 共 {len(chapters)} 个章节")
        
        # Step 3: 多路并发处理各章节
        logger.info("[Step 3] 并发处理各章节...")
        chapter_results = await self._process_chapters_concurrently(course_name, chapters)
        
        # Step 4: 合并结果
        logger.info("[Step 4] 合并结果...")
        final_result = self._merge_results(course_name, chapter_results, total_start)
        
        total_time = time.time() - total_start
        logger.info(f"✅ 全部流程完成，总耗时: {total_time:.2f}s")
        
        return final_result
    
    async def _extract_markdown(self, filedata: str, orig_name: str = None) -> str:
        """Step 1: LLM提取Markdown"""
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

    def _parse_markdown_structure(self, markdown_content: str) -> tuple:
        """Step 2: 内存中切分Markdown结构"""
        lines = markdown_content.strip().split('\n')
        
        course_name = None
        chapters = []
        current_chapter = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # 一级标题：课名
            if line.startswith('# ') and not line.startswith('##'):
                course_name = line[2:].strip()
            
            # 二级标题：章节
            elif line.startswith('## ') and not line.startswith('###'):
                if current_chapter:
                    chapters.append(current_chapter)
                current_chapter = {
                    'title': line[3:].strip(),
                    'content': []
                }
            
            # 三级及以下标题：章节内容
            elif current_chapter and (line.startswith('#') or line):
                current_chapter['content'].append(line)
        
        # 添加最后一个章节
        if current_chapter:
            chapters.append(current_chapter)
        
        return course_name, chapters
    
    async def _process_chapters_concurrently(self, course_name: str, chapters: List[dict]) -> List[dict]:
        """Step 3: 多路并发处理各章节"""
        tasks = [
            self._process_single_chapter(course_name, chapter)
            for chapter in chapters
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理可能的异常
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
        
        # 构建Prompt
        prompt = JSON_EXTRACTION_PROMPT_TEMPLATE.format(课程名=course_name)
        
        # 构建输入内容
        input_content = f"## {chapter_title}\n{chapter_content}"
        
        # 调用LLM
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一位课程大纲分析专家，擅长从教学大纲中提取结构化信息。"},
                {"role": "user", "content": f"{prompt}\n\n【输入内容】\n{input_content}"}
            ],
            timeout=300
        )
        
        result_json = json.loads(response.choices[0].message.content)
        
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
        """Step 4: 合并所有章节结果，并去除重复的lexicon元素"""
        total_prompt_tokens = 0
        total_completion_tokens = 0
        
        keywords = []
        
        # 用于全局去重的lexicon集合
        seen_lexicons = set()
        
        for result in chapter_results:
            chapter_data = result['result']
            usage = result['usage']
            
            # 统计token使用量
            total_prompt_tokens += usage['prompt_tokens']
            total_completion_tokens += usage['completion_tokens']
            
            # 对章节内的lexicon进行去重处理
            if 'content' in chapter_data:
                for module in chapter_data['content']:
                    for module_name, items in module.items():
                        if isinstance(items, list):
                            for item in items:
                                if 'lexicon' in item and isinstance(item['lexicon'], list):
                                    # 去重：保留未出现过的lexicon
                                    unique_lexicons = []
                                    for lex in item['lexicon']:
                                        if lex not in seen_lexicons:
                                            seen_lexicons.add(lex)
                                            unique_lexicons.append(lex)
                                    # 随机打乱顺序（实现随机保留的效果）
                                    import random
                                    random.shuffle(unique_lexicons)
                                    item['lexicon'] = unique_lexicons
            
            keywords.append(chapter_data)
        
        # 构建最终响应
        final_result = {
            #'model': self.model, # 不对外暴露模型
            'result': keywords,
            'usage': {
                'prompt_tokens': total_prompt_tokens,
                'completion_tokens': total_completion_tokens,
                'total_tokens': total_prompt_tokens + total_completion_tokens
            }
        }
        
        return final_result


# ============ 便捷函数 ============

async def run_llm_pipeline(filedata: str, orig_name: str = None) -> dict:
    """
    运行LLM Pipeline的便捷函数

    Args:
        filedata: PDF 文件的 base64 字符串
        orig_name: 原始文件名（不含后缀，可选）

    Returns:
        处理结果字典
    """
    pipeline = LLMPipeline()
    return await pipeline.run(filedata, orig_name)
