"""课程知识脑图生成。"""

import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Tuple

import json_repair
from openai import AsyncOpenAI
from pydantic import BaseModel, validator
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.prompts.mindmap import (
    MINDMAP_SUMMARY_SYSTEM,
    MINDMAP_SUMMARY_USER_TEMPLATE,
    MINDMAP_SYSTEM_PROMPT,
)

logger = get_logger(__name__)


# 数据模型

class TextSegment(BaseModel):
    text: str


class Node(BaseModel):
    id: str
    label: str
    children: Optional[List[Any]] = None


class SegmentResult(BaseModel):
    key_points: str
    document_skims: Dict[str, str]
    nodes: Node

    @validator("document_skims", pre=True)
    def _normalize_skims(cls, v):
        if isinstance(v, list) and len(v) > 0:
            v = v[0]
        assert isinstance(v, dict), "document_skims 应为 dict"
        return v


class SummaryOut(BaseModel):
    full_overview: str
    overall_label: str


# 工具函数

def split_into_4_parts(lst: list) -> list:
    n = len(lst)
    k, m = divmod(n, 4)
    result, start = [], 0
    for i in range(4):
        end = start + k + (1 if i < m else 0)
        result.append(lst[start:end])
        start = end
    return [p for p in result if p]


def build_user_prompt(idx: int, segs: List[TextSegment]) -> str:
    node_id = idx + 1
    header = f"node_id:{node_id}"
    lines = [s.text for s in segs]
    hints = '（请不要把 label 写成\u201c子主题/孙主题\u201d等占位词，必须是实际主题名称）'
    return header + "\n" + "\n".join(lines) + "\n" + hints


def guard(seg_dict: dict) -> bool:
    """校验脑图结果结构。"""
    try:
        assert "key_points" in seg_dict
        assert "document_skims" in seg_dict
        assert "nodes" in seg_dict

        kp = seg_dict["key_points"]
        assert isinstance(kp, str) and 4 <= len(kp) <= 120

        ds = seg_dict["document_skims"]
        assert ds.get("overview") and ds.get("content")

        def check_node(node: dict, depth: int = 0):
            assert node.get("id"), f"节点 id 为空 depth={depth}"
            assert node.get("label"), f"节点 label 为空 depth={depth}"
            if depth >= 1:
                assert not re.search(r"(子主题|孙主题)", node["label"]), "label 含占位词"
            children = node.get("children")
            if children is not None:
                assert isinstance(children, list)
                for c in children:
                    check_node(c, depth + 1)

        check_node(seg_dict["nodes"])

        return True
    except AssertionError as e:
        logger.warning(f"guard 校验失败: {e}")
        return False


def strip_think_blocks(text: str) -> str:
    return re.sub(r"(?is)<\s*think\s*>.*?<\s*/\s*think\s*>", "", text).strip()


def sum_usage(usages: List[Dict]) -> Dict:
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for u in usages:
        if u:
            total["prompt_tokens"] += u.get("prompt_tokens", 0)
            total["completion_tokens"] += u.get("completion_tokens", 0)
            total["total_tokens"] += u.get("total_tokens", 0)
    return total


_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncOpenAI(base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY)
    return _client


async def chat_raw(
    *,
    user_prompt: str = "",
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float = 0.8,
    presence_penalty: float = 1.5,
    response_format: Optional[dict] = None,
    extra_body: Optional[dict] = None,
) -> Tuple[str, Dict]:
    settings = get_settings()
    client = _get_client()
    _model = model or settings.LLM_MODEL

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    kwargs = dict(
        model=_model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        presence_penalty=presence_penalty,
        timeout=3600,
    )
    if response_format:
        kwargs["response_format"] = response_format
    if extra_body:
        kwargs["extra_body"] = extra_body

    resp = await client.chat.completions.create(**kwargs)
    content = strip_think_blocks(resp.choices[0].message.content or "")
    usage = {
        "prompt_tokens": resp.usage.prompt_tokens,
        "completion_tokens": resp.usage.completion_tokens,
        "total_tokens": resp.usage.total_tokens,
    }
    return content, usage


# 单段调用

async def _call_one_attempt(
    prompt: str,
    model: str,
    semaphore: asyncio.Semaphore,
) -> Tuple[Optional[SegmentResult], Dict]:
    async with semaphore:
        try:
            content, usage = await chat_raw(
                user_prompt=prompt,
                system_prompt=MINDMAP_SYSTEM_PROMPT,
                model=model,
                max_tokens=2048,
                temperature=0.7,
                top_p=0.8,
                presence_penalty=1.5,
            )
        except Exception as e:
            logger.warning(f"LLM 调用失败: {e}")
            return None, {}

    try:
        data = json.loads(json_repair.repair_json(content))
        ds = data.get("document_skims")
        if isinstance(ds, list) and len(ds) > 0:
            data["document_skims"] = ds[0]
        seg = SegmentResult(**data)
        if not guard(data):
            logger.warning("guard 校验未通过，将重试")
            return None, usage
        return seg, usage
    except Exception as e:
        logger.warning(f"单段处理失败: {e}")
        return None, usage


# 并发重试

async def run_until_all_pass(
    parts: List[List[TextSegment]],
    model: str,
    concurrency: int = 4,
    max_rounds: int = 5,
) -> Tuple[List[SegmentResult], List[Dict]]:
    semaphore = asyncio.Semaphore(concurrency)
    prompts = [build_user_prompt(i, segs) for i, segs in enumerate(parts)]
    n = len(prompts)

    results: List[Optional[SegmentResult]] = [None] * n
    usages: List[Dict] = [{} for _ in range(n)]
    pending = list(range(n))

    for round_idx in range(max_rounds):
        if not pending:
            break
        logger.info(f"脑图生成 Round {round_idx + 1}，待处理段数: {len(pending)}")

        tasks = [_call_one_attempt(prompts[i], model, semaphore) for i in pending]
        round_results = await asyncio.gather(*tasks)

        still_pending = []
        for idx, (seg, usage) in zip(pending, round_results):
            usages[idx]["prompt_tokens"] = usages[idx].get("prompt_tokens", 0) + usage.get("prompt_tokens", 0)
            usages[idx]["completion_tokens"] = usages[idx].get("completion_tokens", 0) + usage.get("completion_tokens", 0)
            usages[idx]["total_tokens"] = usages[idx].get("total_tokens", 0) + usage.get("total_tokens", 0)
            if seg is not None:
                results[idx] = seg
            else:
                still_pending.append(idx)

        pending = still_pending

    if pending:
        raise RuntimeError(f"仍有 {len(pending)}/{n} 条未通过校验，放弃。")

    return results, usages


# 汇总摘要

def _validate_summary(data: dict):
    assert "full_overview" in data and "overall_label" in data
    assert "概要" not in data["full_overview"]
    assert "本课程" in data["full_overview"] or "This Course" in data["full_overview"]
    assert "总标题" not in data["overall_label"]


@retry(
    retry=retry_if_exception_type((json.JSONDecodeError, Exception)),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(min=1, max=4),
    reraise=True,
)
async def call_summary_ex(key_points: List[str], model: str) -> Tuple[Dict, Dict]:
    user_prompt = MINDMAP_SUMMARY_USER_TEMPLATE.format(
        key_points_json=json.dumps(key_points, ensure_ascii=False)
    )
    content, usage = await chat_raw(
        user_prompt=user_prompt,
        system_prompt=MINDMAP_SUMMARY_SYSTEM,
        model=model,
        max_tokens=512,
        temperature=0.7,
        top_p=0.8,
        presence_penalty=1.5,
    )
    data = json.loads(json_repair.repair_json(content))
    SummaryOut(**data)
    _validate_summary(data)
    return data, usage


# 主入口

async def generate_course_mindmap(
    segments: List[dict],
    *,
    model: Optional[str] = None,
    concurrency: int = 4,
    max_rounds: int = 5,
) -> Tuple[Dict, Dict]:
    """生成完整课程脑图。"""
    settings = get_settings()
    _model = model or settings.LLM_MODEL

    segs = [TextSegment(**{k: v for k, v in s.items() if k == "text"}) for s in segments]
    parts = split_into_4_parts(segs)

    seg_results, seg_usages = await run_until_all_pass(parts, _model, concurrency, max_rounds)

    key_points = [s.key_points for s in seg_results]
    summary_result, summary_usage = await call_summary_ex(key_points, _model)

    document_skims = [s.document_skims for s in seg_results]
    nodes = [s.nodes.model_dump() for s in seg_results]

    result = {
        "full_overview": summary_result["full_overview"],
        "key_points": key_points,
        "document_skims": document_skims,
        "mindmap": {
            "overall_label": summary_result["overall_label"],
            "nodes": nodes,
        },
    }

    total_usage = sum_usage(seg_usages + [summary_usage])
    return result, total_usage
