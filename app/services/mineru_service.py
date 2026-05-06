"""MinerU 文档解析服务。

调用 MinerU 的 /file_parse 接口将文档/图片转为 Markdown，
并对结果做后处理清洗（移除图片引用、details 标签等），
得到干净的文本内容供后续 LLM 处理。
"""

import re
from pathlib import Path
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# 需要从 md_content 中清除的模式
# 1. 图片引用：![...](images/xxx.jpg) 或 ![...](...)
# 2. <details>...</details> 标签块
_IMAGE_PATTERN = re.compile(r"!\[.*?\]\(images/[^\)]+\)\s*")
_DETAILS_PATTERN = re.compile(r"<details>.*?</details>", re.DOTALL)
_TEXT_IMAGE_PATTERN = re.compile(r"<details>\s*<summary>text_image</summary>.*?</details>", re.DOTALL)
_GENERIC_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\([^\)]+\)\s*")


def clean_markdown(md_content: str) -> str:
    """清洗 MinerU 输出的 Markdown 内容。

    移除：
    - 图片引用（![...](images/xxx.jpg)）
    - <details><summary>text_image</summary>...</details> 标签块
    - 其他 <details>...</details> 标签块
    - 多余的空行合并
    """
    text = md_content

    # 先移除 text_image details 块（更具体的模式）
    text = _TEXT_IMAGE_PATTERN.sub("", text)

    # 再移除剩余的 details 块
    text = _DETAILS_PATTERN.sub("", text)

    # 移除图片引用
    text = _GENERIC_IMAGE_PATTERN.sub("", text)

    # 合并连续空行为最多两个换行
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


async def parse_file_with_mineru(file_path: str, filename: Optional[str] = None) -> str:
    """调用 MinerU API 解析文档，返回清洗后的 Markdown 文本。

    Args:
        file_path: 本地文件路径
        filename: 可选的原始文件名

    Returns:
        清洗后的 Markdown 纯文本
    """
    settings = get_settings()
    url = f"{settings.MINERU_BASE_URL}{settings.MINERU_PARSE_ENDPOINT}"
    timeout = settings.MINERU_TIMEOUT

    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    fname = filename or file_path_obj.name

    logger.info(f"调用 MinerU 解析文件: {fname} -> {url}")

    async with httpx.AsyncClient(timeout=timeout) as client:
        with open(file_path, "rb") as f:
            files = {"files": (fname, f)}
            response = await client.post(url, files=files)

    response.raise_for_status()
    data = response.json()

    status = data.get("status", "")
    if status != "completed":
        error = data.get("error", "未知错误")
        raise RuntimeError(f"MinerU 解析未完成: status={status}, error={error}")

    # 从 results 中提取 md_content
    results = data.get("results", {})
    if not results:
        raise RuntimeError("MinerU 返回结果为空")

    # 拼接所有文件的 md_content（通常只有一个）
    md_parts = []
    for file_key, file_result in results.items():
        md_content = file_result.get("md_content", "")
        if md_content:
            md_parts.append(md_content)

    if not md_parts:
        raise RuntimeError("MinerU 返回的 md_content 为空")

    raw_md = "\n\n".join(md_parts)
    logger.info(f"MinerU 原始 md_content 长度: {len(raw_md)}")

    # 清洗
    cleaned = clean_markdown(raw_md)
    logger.info(f"清洗后文本长度: {len(cleaned)}")

    return cleaned


async def parse_bytes_with_mineru(file_bytes: bytes, filename: str) -> str:
    """调用 MinerU API 解析文件字节流，返回清洗后的 Markdown 文本。

    Args:
        file_bytes: 文件的二进制内容
        filename: 原始文件名

    Returns:
        清洗后的 Markdown 纯文本
    """
    settings = get_settings()
    url = f"{settings.MINERU_BASE_URL}{settings.MINERU_PARSE_ENDPOINT}"
    timeout = settings.MINERU_TIMEOUT

    logger.info(f"调用 MinerU 解析文件字节流: {filename} -> {url}")

    async with httpx.AsyncClient(timeout=timeout) as client:
        files = {"files": (filename, file_bytes)}
        response = await client.post(url, files=files)

    response.raise_for_status()
    data = response.json()

    status = data.get("status", "")
    if status != "completed":
        error = data.get("error", "未知错误")
        raise RuntimeError(f"MinerU 解析未完成: status={status}, error={error}")

    results = data.get("results", {})
    if not results:
        raise RuntimeError("MinerU 返回结果为空")

    md_parts = []
    for file_key, file_result in results.items():
        md_content = file_result.get("md_content", "")
        if md_content:
            md_parts.append(md_content)

    if not md_parts:
        raise RuntimeError("MinerU 返回的 md_content 为空")

    raw_md = "\n\n".join(md_parts)
    logger.info(f"MinerU 原始 md_content 长度: {len(raw_md)}")

    cleaned = clean_markdown(raw_md)
    logger.info(f"清洗后文本长度: {len(cleaned)}")

    return cleaned
