# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此代码库中工作时提供指导。

## 项目概述

教学大纲四要点核心内容提取系统 - 基于 FastAPI 的智能系统，自动分析教学大纲文档并提取四个关键模块：**基本要求**、**教学重点**、**教学难点**、**课程思政**，同时为每个知识点生成结构化词库。

## 开发命令

### 运行应用

```bash
# 开发模式（自动重载）
uvicorn app.main:app --reload --port 8000

# 直接执行
python -m app.main

# 生产模式（多进程）
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### 测试

```bash
# 快速测试（无需启动服务器）
python app/tests/test_quick.py

# 完整测试（需要启动服务器）
python app/tests/test_quick.py --full

# 使用客户端工具测试
python app/tests/test_client.py app/tests/data/海洋学院-SR113026-海洋油气地质学.pdf

# 检查服务器健康状态
python app/tests/test_client.py --health

# 查询任务状态
python app/tests/test_client.py --status <task_id>
```

### 依赖管理

```bash
# 安装所有依赖
pip install -r requirements.txt

# 关键依赖（如果缺失）
pip install pydantic-settings
```

## 系统架构

### 处理管道

系统遵循由 `app/services/pipeline.py` 编排的 4 阶段管道：

1. **文档解析** (`app/services/parsers/document_parser.py`)
   - 将多种格式（PDF、Word、PPT、图片）转换为文本
   - 使用 Dolphin 模型进行 OCR 和版面分析
   - 输出：`{filename}_full.txt`

2. **章节分割** (`app/services/parsers/chapter_splitter.py`)
   - 使用传统模式匹配识别章节边界
   - 提取章节标题和内容
   - 输出：单独的章节文本文件

3. **二级要点分割** (`app/services/parsers/subpoint_splitter.py`)
   - 进一步将章节划分为四个关键模块
   - 使用正则表达式识别模块部分
   - 输出：结构化的要点文件

4. **LLM 提取** (`app/services/summarizer/summary_generator.py`)
   - 调用 LLM 提取关键点、摘要并生成词库
   - 并行处理所有四个模块
   - 使用 `app/prompts/dagang.py` 和 `app/prompts/lexicon.py` 中的提示词

### 核心服务模块

- **`app/services/models/call_llm.py`**: LLM API 封装，包含重试逻辑和错误处理
- **`app/services/models/dolphin_model.py`**: Dolphin OCR 模型集成，用于文档解析
- **`app/services/converters/office_to_pdf.py`**: Office 文档转 PDF
- **`app/services/summarizer/lexicon_generator.py`**: 为知识点生成专业词库

### API 结构

- **文档处理**: `POST /api/v1/document/process`
  - 接受 base64 编码的文件数据
  - 返回 task_id 用于异步追踪

- **任务状态**: `GET /api/v1/document/status/{task_id}`
  - 返回处理状态和结果

- **任务管理**:
  - `GET /api/v1/task/list` - 列出所有任务
  - `DELETE /api/v1/task/{task_id}` - 删除任务

## 配置

### config.toml

主配置文件控制：

- **LLM 设置**: 模型名称、API 密钥、base URL、最大 token 数、温度
- **Dolphin 模型路径**: `parsermodel` - OCR 模型的路径
- **分块参数**: chunk_size、overlap、batch_size 用于文本处理
- **日志**: 级别、格式、文件路径
- **GPU**: 通过代码中的 `CUDA_VISIBLE_DEVICES` 设置（默认："1"）

### 环境变量

从 `.env.example` 创建 `.env` 文件用于敏感配置（API 密钥等）

## 重要实现细节

### 异步任务处理

任务存储在 `app/api/v1/endpoints/document.py` 的内存中，使用全局 `tasks` 字典。这适用于单实例部署，但在生产环境的多实例设置中应替换为 Redis/数据库。

### 输出结构

每个处理的文档生成以下结构的结果：
```python
{
    "model": "doubao-seed-2-0-pro-260215",
    "id": "doc-{filename}",
    "result": {
        "keywords": [
            {
                "chapter": "第一章 绪论",
                "content": {
                    "basic": [{"title": "...", "summary": "...", "lexicon": [...]}],
                    "key_points": [...],
                    "difficult_points": [...],
                    "politics": [...]
                }
            }
        ],
        "finished_time": 1709876543,
        "process_time_ms": 12580,
        "finished_reason": "stop"
    },
    "usage": {"prompt_tokens": 8500, "completion_tokens": 2400, "total_tokens": 10900}
}
```

### 临时文件

管道在处理过程中创建 `output_all/` 目录，成功时自动清理。失败时保留以便调试。

## 常见模式

### 添加新的提示词模板

1. 在 `app/prompts/` 中创建新文件（例如 `new_module.py`）
2. 定义返回格式化字符串的提示词函数
3. 在相关服务模块中导入并使用

### 修改 LLM 调用

所有 LLM 交互都通过 `app/services/models/call_llm.py`。`call_llm()` 函数处理：
- 指数退避的重试逻辑
- Token 使用量追踪
- 错误处理和日志记录
- 流式支持（如需要）

### 使用文档解析器

Dolphin 模型路径必须在 `config.toml` 中正确配置。模型在每次管道运行时加载一次，用于 PDF 文档的 OCR/版面分析。

## 测试数据

测试文档位于 `app/tests/data/`：
- 用于测试完整管道的 PDF 样本
- 用于格式转换测试的 Word/PPT 文档
- 结果保存到 `app/tests/results/` 作为 JSON 文件

## 依赖说明

本项目使用：
- **PyTorch + CUDA**: 用于 Dolphin 模型推理（需要 GPU）
- **Transformers**: 用于模型加载和分词
- **PyMuPDF**: 用于 PDF 文本提取
- **python-pptx**: 用于 PowerPoint 处理
- **FastAPI + Uvicorn**: 用于 Web 服务
- **OpenAI client**: 用于 LLM API 调用（兼容多种提供商）
