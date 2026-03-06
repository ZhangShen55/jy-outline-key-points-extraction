# LLM直接提取方案设计（替代Dolphin）

## 方案概述

使用大模型（Doubao/Volces）直接提取教学大纲内容，替代原有的Dolphin模型解析流程。

## 整体流程

```
┌─────────────────────────────────────────────────────────────┐
│  输入：上传的文档（PDF/Word等）                              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 1: LLM提取Markdown                                    │
│  - 大模型直接读取文档                                         │
│  - 提取为规范Markdown格式                                     │
│  - 设置长超时时间（文档处理可能较慢）                          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 2: 内存中切分（不使用临时文件）                        │
│  - 根据 # ## 符号切分                                         │
│  - 提取大标题（课名）：一级标题 #                            │
│  - 提取各个章节：二级标题 ##                                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 3: 多路并发处理                                         │
│  - 每个章节（课名+章节内容）独立处理                          │
│  - 调用LLM提取四要点（基本要求/教学重点/教学难点/课程思政）  │
│  - 根据章节数多路并发（asyncio.gather）                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 4: 合并结果                                             │
│  - 聚合所有章节的处理结果                                     │
│  - 合并usage统计（prompt_tokens/completion_tokens）          │
│  - 返回标准JSON格式                                           │
└─────────────────────────────────────────────────────────────┘
```

## 详细设计

### Step 1: LLM提取Markdown

**输入**：文档文件（PDF/Word/图片等）
**输出**：规范Markdown格式文本

**Prompt设计**：
```
你是一位专业的教学大纲解析专家。请仔细阅读上传的教学大纲文档，提取以下信息并以标准Markdown格式输出：

【提取规则】
1. 课程名称：提取为一级标题（#）
2. 授课章节：提取为二级标题（##），注意：
   - 只保留理论教学章节
   - 排除实验活动、课程设计等实践环节
3. 章节下的四个模块：基本要求、教学重点、教学难点、课程思政
   - 提取为三级标题（###）
4. 模块下的具体要点：
   - 提炼成7-15字的简短标题
   - 提取为四级标题（####）

【输出格式示例】
# 石油与天然气地质学

## 第1章 绪论

### 基本要求
#### 了解课程体系及学科发展概况
#### 了解世界及我国油气工业现状

### 教学重点
#### 中国油气资源战略地位

### 教学难点
#### 非常规油气对传统理论的挑战

### 课程思政
#### 以石油工业史为切入点

【注意事项】
1. 如果某章节下没有四个模块中的某个，则跳过该模块
2. 如果模块下没有具体内容，则不创建四级标题
3. 确保所有标题层级正确，不要跳级
4. 只输出Markdown内容，不要输出任何解释性文字
```

**实现注意**：
- 设置长超时时间（如300秒），文档处理可能较慢
- 使用`client.responses.create`或`client.chat.completions.create`
- 处理可能的超时和重试逻辑

### Step 2: 内存中切分

**输入**：Markdown文本
**输出**：课名 + 章节列表

**切分逻辑**：
```python
import re

def parse_markdown_structure(markdown_text):
    """
    解析Markdown结构，提取课名和章节
    """
    lines = markdown_text.strip().split('\n')
    
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
    
    return {
        'course_name': course_name,
        'chapters': chapters
    }
```

### Step 3: 多路并发处理

**输入**：课名 + 单个章节
**输出**：该章节的四要点提取结果

**并发处理**：
```python
import asyncio

async def process_chapter(course_name, chapter, client):
    """
    处理单个章节，提取四要点
    """
    prompt = build_chapter_prompt(course_name, chapter)
    
    response = await client.chat.completions.create(
        model="doubao-seed-2-0-pro-260215",
        messages=[
            {"role": "system", "content": "你是一位课程大纲分析专家..."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"}
    )
    
    return {
        'chapter_title': chapter['title'],
        'result': json.loads(response.choices[0].message.content),
        'usage': response.usage
    }

async def process_all_chapters(course_name, chapters, client):
    """
    并发处理所有章节
    """
    tasks = [
        process_chapter(course_name, chapter, client)
        for chapter in chapters
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 处理可能的异常
    successful_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"Chapter {i} failed: {result}")
        else:
            successful_results.append(result)
    
    return successful_results
```

### Step 4: 合并结果

**输入**：所有章节的处理结果
**输出**：标准JSON格式

**合并逻辑**：
```python
def merge_results(course_name, chapter_results):
    """
    合并所有章节的处理结果
    """
    total_prompt_tokens = 0
    total_completion_tokens = 0
    
    keywords = []
    
    for result in chapter_results:
        chapter_title = result['chapter_title']
        chapter_data = result['result']
        usage = result['usage']
        
        # 统计token使用量
        total_prompt_tokens += usage.prompt_tokens
        total_completion_tokens += usage.completion_tokens
        
        # 构建章节数据结构
        chapter_entry = {
            'chapter': chapter_title,
            'content': chapter_data
        }
        keywords.append(chapter_entry)
    
    # 构建最终响应
    final_result = {
        'model': 'doubao-seed-2-0-pro-260215',
        'result': {
            'keywords': keywords,
            'finished_time': int(time.time()),
            'process_time_ms': 0  # 需要计算
        },
        'usage': {
            'prompt_tokens': total_prompt_tokens,
            'completion_tokens': total_completion_tokens,
            'total_tokens': total_prompt_tokens + total_completion_tokens
        }
    }
    
    return final_result
```

## 总结

你的新方案是**完全可行的**，而且简化了架构（去掉Dolphin依赖）。关键点：

1. **LLM直接提取Markdown**：可行，Doubao对中文文档理解能力强
2. **内存中切分**：无需临时文件，直接用字符串处理
3. **多路并发**：asyncio并发处理各章节，提高效率
4. **合并结果**：标准JSON格式，与现有接口兼容

需要我帮你实现这个新pipeline吗？或者你先看看这个设计有没有问题？

说"toggle to Act mode"，我来开始实现代码。