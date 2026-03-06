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
from pathlib import Path
from typing import List, Dict, Any, Optional
import base64

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# ============ Prompt 1: 提取Markdown ============

MARKDOWN_EXTRACTION_PROMPT = """这是一份教学大纲，请你只要提取其中的课程名称（一级标题），授课章节（二级标题，不需要实验活动章节）如果章节下有如下内容，就作为三级标题：基本要求、教学重点、教学难点、教学思政，三级标题下还有内容，就作为四级标题（提炼成7-15字），md格式，一级标题使用#号，二级标题使用##号，三级标题使用###号，四级标题使用####号。

注意事项：
1. 只输出Markdown内容，不要输出任何解释性文字
2. 如某模块不存在则跳过，不要输出空模块
3. 确保标题层级正确，不要跳级"""


# ============ Prompt 2: 提取JSON ============

JSON_EXTRACTION_PROMPT_TEMPLATE = """请处理{课程名}教学大纲的部分内容，完成以下任务：
1. 提取章名（chapter）：需去除"第x章"字眼，仅保留章节核心名称；提取章节编号（num）：为int类型，从输入的## 标题中取阿拉伯数字。
2. 识别内容中实际存在的模块（仅从基本要求（basic）、教学重点（keypoints）、教学难点（difficulty）、教学思政（politics）4类中选取），每个模块下按层级处理子项，如果输入的内容中4项有缺失，缺失的类别忽略，不要求整理输出：
   - 每个子项需提取小标题（title）、分配连续的阿拉伯数字序号（num，int类型，从1开始依次递增）；
   - 为每个子项生成20字以内的简要描述（summary），需精准概括小标题核心含义；
   - 为每个子项生成5-6个、每个4-6字的相关基础词库（lexicon），用于知识点匹配检索。
输入示例：
## 第2章 章节名
### 基本要求
#### 要求内容小标题1
#### 要求内容小标题2
#### 要求内容小标题3
### 教学重点
#### 教学重点小标题1
#### 教学重点小标题2
### 教学难点
#### 教学难点小标题1
### 课程思政
#### 课程思政内容小标题1
#### 课程思政内容小标题2
#### 课程思政内容小标题3

输出格式（严格遵循以下Schema，字段名不可修改，无多余字段，仅保留实际存在的模块）：
{{
    "chapter": "章节核心名称",
    "num": 章节编号,
    "content": [
        {{
            "basic": [
                {{
                    "title": "子项小标题",
                    "num": 1,
                    "summary": "20字以内简要描述",
                    "lexicon": ["词库1", "词库2", "词库3", "词库4",...]
                }}
            ]
        }},
        {{
            "keypoints": [
                {{
                    "title": "子项小标题",
                    "num": 2,
                    "summary": "20字以内简要描述",
                    "lexicon": ["词库1", "词库2", "词库3", "词库4",...]
                }}
            ]
        }},
        {{
            "difficulty": [
                {{
                    "title": "子项小标题",
                    "num": 3,
                    "summary": "20字以内简要描述",
                    "lexicon": ["词库1", "词库2", "词库3", "词库4",...]
                }}
            ]
        }},
        {{
            "politics": [
                {{
                    "title": "子项小标题",
                    "num": 4,
                    "summary": "20字以内简要描述",
                    "lexicon": ["词库1", "词库2", "词库3", "词库4",...]
                }}
            ]
        }}
    ]
}}

输出要求：仅返回符合上述格式的纯JSON文本，无Markdown代码块、无额外说明、无格式错误。"""


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
        
    async def run(self, file_path: Path, orig_name: str = None) -> dict:
        """
        运行完整Pipeline
        
        Args:
            file_path: 文档路径
            orig_name: 原始文件名（可选）
            
        Returns:
            处理结果字典
        """
        total_start = time.time()
        
        # Step 1: LLM提取Markdown
        logger.info("[Step 1] LLM提取Markdown...")
        markdown_content = await self._extract_markdown(file_path,orig_name)
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
    
    async def _extract_markdown(self, file_path: Path,orig_name: str = None) -> str:
        """Step 1: LLM提取Markdown"""
        # 读取文件并转base64

        print("文件路径:", file_path)

        with open(file_path, "rb") as f:
            file_content = f.read()
        base64_content = base64.b64encode(file_content).decode('utf-8')
        
        # print(f"文件名: {file_path.name}-----{orig_name}")

        # 获取文件MIME类型
        mime_type = self._get_mime_type(file_path)
        print(f"mime_type: {mime_type}")
        print(orig_name+".pdf")
        
        
        # 调用LLM
        response = await self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "file_data": f"data:{mime_type};base64,{base64_content}",
                            "filename": orig_name+".pdf"
                        },
                        {
                            "type": "input_text",
                            "text": MARKDOWN_EXTRACTION_PROMPT
                        }
                    ]
                }
            ],
            timeout=600  # 5分钟超时
        )
        
        return response.output[1].content[0].text
    
    def _get_mime_type(self, file_path: Path) -> str:
        """根据文件扩展名获取MIME类型"""
        suffix = file_path.suffix.lower()
        mime_types = {
            '.pdf': 'application/pdf',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.ppt': 'application/vnd.ms-powerpoint',
            '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            '.txt': 'text/plain',
        }
        return mime_types.get(suffix, 'application/octet-stream')
    
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
            # response_format={"type": "json_object"},
            timeout=120  
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
            'model': self.model,
            'result': {
                'keywords': keywords,
                'finished_time': int(time.time()),
                'process_time_ms': int((time.time() - start_time) * 1000)
            },
            'usage': {
                'prompt_tokens': total_prompt_tokens,
                'completion_tokens': total_completion_tokens,
                'total_tokens': total_prompt_tokens + total_completion_tokens
            }
        }
        
        return final_result


# ============ 便捷函数 ============

async def run_llm_pipeline(file_path: Path, orig_name: str = None) -> dict:
    """
    运行LLM Pipeline的便捷函数
    
    Args:
        file_path: 文档路径
        orig_name: 原始文件名（可选）
        
    Returns:
        处理结果字典
    """
    pipeline = LLMPipeline()
    return await pipeline.run(file_path, orig_name)
