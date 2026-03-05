"""
使用 OpenAI SDK 调用 LLM API
支持 OpenAI、Doubao、SiliconFlow 等兼容 OpenAI API 的服务
"""
import re
import json
import time
from openai import OpenAI
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def call_llm(
    model: str,
    user_prompt: str,
    api_key: str,
    system_prompt: str = None,
    max_tokens: int = 1000,
    temperature: float = 0.2,
    base_url: str = None,
    return_usage: bool = False,
    max_retries: int = 3,
    timeout: int = 180
):
    """
    使用 OpenAI SDK 调用 LLM API

    Args:
        model: 模型名称
        user_prompt: 用户提示词
        api_key: API 密钥
        system_prompt: 系统提示词（可选）
        max_tokens: 最大生成 token 数
        temperature: 温度参数
        base_url: API 基础 URL（注意：不要包含 /chat/completions，SDK 会自动添加）
        return_usage: 是否返回 token 使用统计
        max_retries: 最大重试次数
        timeout: 超时时间（秒）

    Returns:
        如果 return_usage=True: (content, usage_dict)
        如果 return_usage=False: content
    """
    # 处理 base_url：移除末尾的 /chat/completions（如果有）
    # OpenAI SDK 会自动添加 /chat/completions
    if base_url:
        base_url = base_url.rstrip('/')
        if base_url.endswith('/chat/completions'):
            base_url = base_url[:-len('/chat/completions')]
            logger.debug(f"移除 base_url 末尾的 /chat/completions: {base_url}")

    # 创建 OpenAI 客户端
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=max_retries
    )

    # 构建消息
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    # 调用 API
    start_time = time.time()
    try:
        logger.debug(f"调用 LLM: model={model}, max_tokens={max_tokens}, temperature={temperature}")

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature
        )

        elapsed = time.time() - start_time
        logger.debug(f"LLM 响应成功，耗时: {elapsed:.2f}s")

        # 提取内容
        content = response.choices[0].message.content

        # 提取 token 使用统计
        if return_usage:
            usage_info = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }
            return content, usage_info
        else:
            return content

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"LLM 调用失败，耗时: {elapsed:.2f}s, 错误: {e}")
        raise


def call_llm_with_retry(
    model: str,
    user_prompt: str,
    api_key: str,
    system_prompt: str = None,
    max_tokens: int = 1000,
    temperature: float = 0.2,
    base_url: str = None,
    return_usage: bool = False,
    max_retries: int = 10,
    timeout: int = 180,
    retry_delay: float = 2.0
):
    """
    带自定义重试逻辑的 LLM 调用（用于兼容旧代码）

    Args:
        retry_delay: 重试间隔（秒）
        其他参数同 call_llm

    Returns:
        同 call_llm
    """
    for attempt in range(1, max_retries + 1):
        try:
            return call_llm(
                model=model,
                user_prompt=user_prompt,
                api_key=api_key,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                base_url=base_url,
                return_usage=return_usage,
                max_retries=1,  # 内部不重试，由外层控制
                timeout=timeout
            )
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"[{attempt}/{max_retries}] LLM 调用失败，{retry_delay}秒后重试... 错误: {e}")
                time.sleep(retry_delay)
            else:
                logger.error(f"[{attempt}/{max_retries}] LLM 调用失败，已达最大重试次数")
                raise


def extract_json_from_text(text: str) -> dict:
    """
    从文本中提取 JSON 对象

    支持以下格式：
    1. 纯 JSON
    2. Markdown 代码块中的 JSON
    3. 文本中嵌入的 JSON 数组

    Args:
        text: 包含 JSON 的文本

    Returns:
        解析后的 JSON 对象，失败返回 None
    """
    if not text:
        return None

    # 尝试 1: 提取 Markdown 代码块中的 JSON
    code_block_pattern = r'```(?:json)?\s*([\s\S]*?)```'
    code_match = re.search(code_block_pattern, text)
    if code_match:
        text = code_match.group(1).strip()

    # 尝试 2: 直接解析 JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试 3: 提取 JSON 对象 {...}
    try:
        start_idx = text.find('{')
        if start_idx != -1:
            bracket_count = 0
            for i in range(start_idx, len(text)):
                if text[i] == '{':
                    bracket_count += 1
                elif text[i] == '}':
                    bracket_count -= 1
                    if bracket_count == 0:
                        json_str = text[start_idx:i+1]
                        return json.loads(json_str)
    except:
        pass

    # 尝试 4: 提取 JSON 数组 [...]
    try:
        start_idx = text.find('[')
        if start_idx != -1:
            bracket_count = 0
            for i in range(start_idx, len(text)):
                if text[i] == '[':
                    bracket_count += 1
                elif text[i] == ']':
                    bracket_count -= 1
                    if bracket_count == 0:
                        json_str = text[start_idx:i+1]
                        return json.loads(json_str)
    except:
        pass

    logger.warning(f"无法从文本中提取 JSON: {text[:100]}...")
    return None


# 向后兼容：保留旧的函数签名
def debug_request_llm(url, **kwargs):
    """
    已废弃：使用 OpenAI SDK 后不再需要此函数
    保留此函数仅为向后兼容
    """
    logger.warning("debug_request_llm 已废弃，请使用 call_llm")
    import requests
    return requests.post(url, **kwargs)
