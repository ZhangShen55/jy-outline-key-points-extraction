"""
课堂语音转写分析管道

处理流程：
1. 生成知识脑图（mindmap_generator）
2. 章节匹配（LLM）
3. 段落合并 + 并发知识点匹配
4. 汇总匹配结果
5. 组装最终输出
"""

import asyncio
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import json_repair

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.prompts.lesson import (
    CHAPTER_MATCH_SYSTEM,
    CHAPTER_MATCH_USER_TEMPLATE,
    SEGMENT_MATCH_SYSTEM,
    SEGMENT_MATCH_USER_TEMPLATE,
)
from app.services.mindmap_generator import (
    chat_raw,
    generate_course_mindmap,
    sum_usage,
)

logger = get_logger(__name__)


# ─── 段落合并 ─────────────────────────────────────────────────────────────────

def merge_text_segments(raw_segments: List[dict], target_chars: int = 150) -> List[dict]:
    """
    两步合并：
    1. 按句末标点（。！？）合并短句 → 句子
    2. 累积到 ~target_chars 字切段
    """
    # Step 1: 按句末标点合并短句
    sentences = []
    buf_text = ""
    buf_bg = None
    buf_ed = None

    for seg in raw_segments:
        text = seg["text"].strip()
        if not text:
            continue
        if buf_bg is None:
            buf_bg = seg["bg"]
        buf_ed = seg["ed"]
        buf_text += text

        if re.search(r"[。！？!?]$", text):
            sentences.append({"text": buf_text, "bg": buf_bg, "ed": buf_ed})
            buf_text = ""
            buf_bg = None
            buf_ed = None

    if buf_text:
        sentences.append({"text": buf_text, "bg": buf_bg, "ed": buf_ed})

    # Step 2: 累积到 target_chars 字切段
    merged = []
    seg_id = 1
    acc_text = ""
    acc_bg = None
    acc_ed = None

    for sent in sentences:
        if acc_bg is None:
            acc_bg = sent["bg"]
        acc_ed = sent["ed"]
        acc_text += sent["text"]

        if len(acc_text) >= target_chars:
            merged.append({
                "seg_id": f"S{seg_id}",
                "text": acc_text,
                "bg": acc_bg,
                "ed": acc_ed,
            })
            seg_id += 1
            acc_text = ""
            acc_bg = None
            acc_ed = None

    if acc_text:
        merged.append({
            "seg_id": f"S{seg_id}",
            "text": acc_text,
            "bg": acc_bg,
            "ed": acc_ed,
        })

    return merged


# ─── 章节匹配 ─────────────────────────────────────────────────────────────────

def _build_chapters_summary(syllabus_result: dict) -> str:
    """将大纲结果压缩为章节摘要字符串。"""
    lines = []
    chapters = syllabus_result.get("result", [])
    for ch in chapters:
        chapter_title = ch.get("chapter", "")
        content = ch.get("content", {})
        titles = []
        for cat in ("basic", "key_points", "difficult_points", "politics"):
            items = content.get(cat, [])
            for item in items:
                t = item.get("title", "")
                if t:
                    titles.append(t)
        lines.append(f"- {chapter_title}：{', '.join(titles[:6])}")
    return "\n".join(lines)


async def match_chapters(
    syllabus_result: dict,
    key_points: List[str],
    model: str,
) -> Tuple[List[dict], Dict]:
    chapters_summary = _build_chapters_summary(syllabus_result)
    key_points_str = "\n".join(f"- {kp}" for kp in key_points)

    user_prompt = CHAPTER_MATCH_USER_TEMPLATE.format(
        chapters_summary=chapters_summary,
        key_points=key_points_str,
    )

    content, usage = await chat_raw(
        user_prompt=user_prompt,
        system_prompt=CHAPTER_MATCH_SYSTEM,
        model=model,
        max_tokens=512,
        temperature=0.3,
        top_p=0.9,
        presence_penalty=0.0,
        # response_format={"type": "json_object"},
    )

    try:
        data = json.loads(json_repair.repair_json(content))
        matched = data.get("matched_chapters", [])
    except Exception as e:
        logger.warning(f"章节匹配解析失败: {e}, content={content[:200]}")
        matched = []

    return matched, usage


# ─── 提取匹配章节的四要点 ─────────────────────────────────────────────────────

def _extract_points_from_chapters(
    syllabus_result: dict,
    matched_chapters: List[dict],
) -> List[dict]:
    """从大纲中提取匹配章节的四要点列表。"""
    matched_titles = {ch.get("chapter", "") for ch in matched_chapters}
    points = []

    for ch in syllabus_result.get("result", []):
        if ch.get("chapter", "") not in matched_titles:
            continue
        content = ch.get("content", {})
        cat_map = {
            "basic": "basic",
            "key_points": "keypoints",
            "difficult_points": "difficulty",
            "politics": "politics",
        }
        for src_key, cat in cat_map.items():
            for item in content.get(src_key, []):
                points.append({
                    "category": cat,
                    "title": item.get("title", ""),
                    "lexicon": item.get("lexicon", []),
                })

    return points


# ─── 段落-知识点匹配 ──────────────────────────────────────────────────────────

async def _match_one_segment(
    seg: dict,
    points: List[dict],
    model: str,
    semaphore: asyncio.Semaphore,
) -> Tuple[Optional[dict], Dict]:
    async with semaphore:
        user_prompt = SEGMENT_MATCH_USER_TEMPLATE.format(
            seg_id=seg["seg_id"],
            bg=seg["bg"],
            ed=seg["ed"],
            full_text=seg["text"],
            points_json=json.dumps(points, ensure_ascii=False),
        )

        try:
            content, usage = await chat_raw(
                user_prompt=user_prompt,
                system_prompt=SEGMENT_MATCH_SYSTEM,
                model=model,
                max_tokens=1024,
                temperature=0.3,
                top_p=0.9,
                presence_penalty=0.0,
                # response_format={"type": "json_object"},
            )
        except Exception as e:
            logger.warning(f"段落匹配 LLM 调用失败 {seg['seg_id']}: {e}")
            return None, {}

    stripped = content.strip()
    if stripped.lower() == "null" or not stripped:
        return None, usage

    try:
        data = json.loads(json_repair.repair_json(stripped))
        if not data or not isinstance(data, dict) or data.get("no_match"):
            return None, usage
        # 确保 full_text 字段存在
        for ms in data.get("matched_segments", []):
            if not ms.get("full_text"):
                ms["full_text"] = seg["text"]
        return data, usage
    except Exception as e:
        logger.debug(f"段落匹配解析失败 {seg['seg_id']}: {e}")
        return None, usage


async def match_segments_to_points(
    merged_segments: List[dict],
    points: List[dict],
    model: str,
    concurrency: int = 8,
) -> Tuple[List[Optional[dict]], List[Dict]]:
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [
        _match_one_segment(seg, points, model, semaphore)
        for seg in merged_segments
    ]
    results = await asyncio.gather(*tasks)
    matches = [r[0] for r in results]
    usages = [r[1] for r in results]
    return matches, usages


# ─── 汇总匹配结果 ─────────────────────────────────────────────────────────────

def _compute_coverage(
    matches: List[Optional[dict]],
    points: List[dict],
    total_segments: int,
) -> dict:
    # 按 category 统计
    cat_totals: Dict[str, int] = {}
    cat_matched: Dict[str, set] = {}
    for p in points:
        cat = p["category"]
        cat_totals[cat] = cat_totals.get(cat, 0) + 1
        cat_matched.setdefault(cat, set())

    matched_point_keys: set = set()
    matched_seg_ids: set = set()

    valid_matches = []
    for m in matches:
        if m is None:
            continue
        valid_matches.append(m)
        key = (m.get("category", ""), m.get("title", ""))
        matched_point_keys.add(key)
        cat = m.get("category", "")
        if cat in cat_matched:
            cat_matched[cat].add(m.get("title", ""))
        for ms in m.get("matched_segments", []):
            matched_seg_ids.add(ms.get("seg_id", ""))

    category_coverage = {}
    for cat, total in cat_totals.items():
        matched_count = len(cat_matched.get(cat, set()))
        pct = f"{matched_count / total * 100:.0f}%" if total > 0 else "0%"
        category_coverage[cat] = {
            "total": total,
            "matched": matched_count,
            "coverage": pct,
        }

    total_points = len(points)
    matched_points = len(matched_point_keys)
    coverage_pct = f"{matched_points / total_points * 100:.0f}%" if total_points > 0 else "0%"
    matched_segs = len(matched_seg_ids)
    seg_pct = f"{matched_segs / total_segments * 100:.2f}%" if total_segments > 0 else "0%"

    ratio = matched_points / total_points if total_points > 0 else 0
    level = "高" if ratio >= 0.7 else ("中" if ratio >= 0.4 else "低")

    return {
        "matches": valid_matches,
        "category_coverage": category_coverage,
        "overall_coverage": {
            "total_points": total_points,
            "matched_points": matched_points,
            "coverage": coverage_pct,
            "total_segments": total_segments,
            "matched_segments": matched_segs,
            "segment_coverage": seg_pct,
            "level": level,
        },
    }


def _build_unmatched_points(matches: List[Optional[dict]], points: List[dict]) -> List[dict]:
    matched_keys = set()
    for m in matches:
        if m:
            matched_keys.add((m.get("category", ""), m.get("title", "")))

    unmatched = []
    for p in points:
        key = (p["category"], p["title"])
        if key not in matched_keys:
            unmatched.append({
                "category": p["category"],
                "title": p["title"],
                "reason": "课堂未涉及该知识点相关内容",
            })
    return unmatched


def _build_summary(overall: dict, category_coverage: dict) -> str:
    covered_cats = [cat for cat, v in category_coverage.items() if v["matched"] > 0]
    uncovered_cats = [cat for cat, v in category_coverage.items() if v["matched"] == 0]
    cat_names = {"basic": "基础类", "keypoints": "重点类", "difficulty": "难点类", "politics": "思政类"}
    covered_str = "、".join(cat_names.get(c, c) for c in covered_cats) if covered_cats else "无"
    uncovered_str = "、".join(cat_names.get(c, c) for c in uncovered_cats) if uncovered_cats else "无"
    return (
        f"本节课共覆盖大纲 {overall['coverage']} 的知识点，"
        f"其中 {covered_str} 知识点有所覆盖，"
        f"{uncovered_str} 知识点未覆盖，"
        f"整体覆盖水平为「{overall['level']}」。"
    )


# ─── 主管道 ───────────────────────────────────────────────────────────────────

async def run_lesson_pipeline(
    syllabus_result: dict,
    text_segments: List[dict],
    filename: str,
) -> dict:
    """
    课堂语音转写分析主管道。

    Args:
        syllabus_result: 大纲提取结果（含 course + result 字段）
        text_segments: 语音转写段落列表 [{text, bg, ed}, ...]
        filename: 课程文件名

    Returns:
        分析结果字典
    """
    settings = get_settings()
    model = settings.LLM_MODEL
    total_start = time.time()
    all_usages = []

    original_count = len(text_segments)
    logger.info(f"[1/5] 生成知识脑图... 原始段数: {original_count}")

    # Step 1: 脑图生成
    mindmap_result, mindmap_usage = await generate_course_mindmap(
        text_segments, model=model
    )
    all_usages.append(mindmap_usage)

    key_points = mindmap_result.get("key_points", [])
    logger.info(f"[2/5] 章节匹配，key_points 数: {len(key_points)}")
    matched_chapters, chapter_usage = await match_chapters(syllabus_result, key_points, model)
    all_usages.append(chapter_usage)

    primary_chapters = [ch.get("num", 0) for ch in matched_chapters if ch.get("num")]
    logger.info(f"匹配章节: {matched_chapters}")

    logger.info("[3/5] 段落合并...")
    merged_segments = merge_text_segments(text_segments)
    logger.info(f"合并后段数: {len(merged_segments)}")

    logger.info("[3c] 提取四要点...")
    points = _extract_points_from_chapters(syllabus_result, matched_chapters)
    logger.info(f"四要点数: {len(points)}")

    logger.info("[3d] 并发段落-知识点匹配...")
    seg_matches, seg_usages = await match_segments_to_points(
        merged_segments, points, model, concurrency=8
    )
    all_usages.extend(seg_usages)

    logger.info("[4/5] 汇总匹配结果...")
    coverage = _compute_coverage(seg_matches, points, len(merged_segments))
    unmatched = _build_unmatched_points(seg_matches, points)
    summary_text = _build_summary(coverage["overall_coverage"], coverage["category_coverage"])

    match_result = {
        "matches": coverage["matches"],
        "unmatched_points": unmatched,
        "category_coverage": coverage["category_coverage"],
        "overall_coverage": coverage["overall_coverage"],
        "summary": summary_text,
    }

    logger.info("[5/5] 组装最终输出...")
    total_usage = sum_usage(all_usages)

    result = {
        "source_file": filename,
        "primary_chapters": primary_chapters,
        "original_segments": original_count,
        "merged_segments_count": len(merged_segments),
        "merged_segments": merged_segments,
        "mindmap": mindmap_result,
        "match_result": match_result,
        "model": model,
        "usage": total_usage,
    }

    elapsed = time.time() - total_start
    logger.info(f"✅ 课堂分析完成，耗时: {elapsed:.2f}s")
    return result
