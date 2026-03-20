"""课堂语音转写分析管道。"""

import asyncio
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import json_repair

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.prompts.lesson import (
    ALERTS_SYSTEM,
    ALERTS_USER_TEMPLATE,
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


# 段落合并

def merge_text_segments(raw_segments: List[dict], target_chars: int = 200) -> List[dict]:
    """
    两步合并：
    1. 按句末标点（。！？）合并短句 → 句子
    2. 累积到 ~target_chars 字切段
    时间字段仅用于结果输出，不参与 LLM 判断。
    """
    # 先合并为完整句子
    sentences = []
    buf_text = ""
    buf_bg = None
    buf_ed = None

    for seg in raw_segments:
        text = seg["text"].strip()
        if not text:
            continue
        if buf_bg is None:
            buf_bg = seg.get("bg")
        buf_ed = seg.get("ed")
        buf_text += text

        if re.search(r"[。！？!?]$", text):
            sentences.append({"text": buf_text, "bg": buf_bg, "ed": buf_ed})
            buf_text = ""
            buf_bg = None
            buf_ed = None

    if buf_text:
        sentences.append({"text": buf_text, "bg": buf_bg, "ed": buf_ed})

    # 再按目标长度切段
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


# 章节匹配

def _flatten_content(content) -> dict:
    """将 content 列表 [{"basic": [...]}, {"keypoints": [...]}] 展平为单个字典。"""
    if isinstance(content, dict):
        return content
    flat = {}
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                flat.update(item)
    return flat


def _build_chapters_summary(syllabus_result: dict) -> str:
    """将大纲结果压缩为章节摘要字符串。"""
    lines = []
    chapters = syllabus_result.get("result", [])
    for ch in chapters:
        chapter_title = ch.get("chapter", "")
        content = _flatten_content(ch.get("content", {}))
        titles = []
        for cat in ("basic", "key_points", "keypoints", "difficult_points", "difficulty", "politics"):
            items = content.get(cat, [])
            for item in items:
                t = item.get("title", "")
                if t:
                    titles.append(t)
        lines.append(f"- {chapter_title}：{', '.join(titles[:6])}")
    return "\n".join(lines)


def _build_skims_text(document_skims) -> str:
    if isinstance(document_skims, dict):
        document_skims = [document_skims]
    if not document_skims:
        return "无"
    lines = []
    for skim in document_skims:
        time_range = skim.get("time", "")
        overview = skim.get("overview", "")
        content = skim.get("content", "")
        lines.append(f"[{time_range}] {overview}\n  → {content}")
    return "\n\n".join(lines)


def _build_mindmap_text(mindmap: dict, indent: int = 0) -> str:
    if not mindmap:
        return "无"
    nodes = mindmap.get("nodes", [])
    if not nodes:
        return "无"
    lines = []
    for node in nodes:
        prefix = "  " * indent
        label = node.get("label", "")
        node_id = node.get("id", "")
        lines.append(f"{prefix}{node_id}. {label}")
        children = node.get("children", [])
        if children:
            for child in children:
                lines.append(_build_mindmap_text({"nodes": [child]}, indent + 1))
    return "\n".join(lines)


async def match_chapters(
    syllabus_result: dict,
    mindmap_result: dict,
    model: str,
) -> Tuple[List[dict], Dict]:
    chapters_summary = _build_chapters_summary(syllabus_result)
    course_name = syllabus_result.get("course", "")
    overview = mindmap_result.get("overview", mindmap_result)
    key_points = overview.get("key_points", [])
    key_points_str = "\n".join(f"- {kp}" for kp in key_points)

    overall_label = overview.get("mindmap", {}).get("overall_label", "")
    skims_text = _build_skims_text(overview.get("document_skims", []))
    mindmap_text = _build_mindmap_text(overview.get("mindmap", {}))


    user_prompt = CHAPTER_MATCH_USER_TEMPLATE.format(
        course_name=course_name,
        chapters_summary=chapters_summary,
        key_points=key_points_str,
        overall_label=overall_label,
        skims_text=skims_text,
        mindmap_text=mindmap_text,
    )

    content, usage = await chat_raw(
        user_prompt=user_prompt,
        system_prompt=CHAPTER_MATCH_SYSTEM,
        model=model,
        max_tokens=512,
        temperature=0.3,
    )

    try:
        data = json.loads(json_repair.repair_json(content))
        matched = data.get("matched_chapters", [])
    except Exception as e:
        logger.warning(f"章节匹配解析失败: {e}, content={content[:200]}")
        matched = []

    return matched, usage


# 提取匹配章节的四要点

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
        chapter_num = ch.get("num", 0)
        content = _flatten_content(ch.get("content", {}))
        cat_map = {
            "basic": "basic",
            "key_points": "keypoints",
            "keypoints": "keypoints",
            "difficult_points": "difficulty",
            "difficulty": "difficulty",
            "politics": "politics",
        }
        for src_key, cat in cat_map.items():
            for item in content.get(src_key, []):
                points.append({
                    "category": cat,
                    "title": item.get("title", ""),
                    "lexicon": item.get("lexicon", []),
                    "chapter_num": chapter_num,
                })

    return points


def _realign_snippet(snippet: str, full_text: str) -> str:
    """将 LLM 返回的 text_snippet 对齐到 full_text 中重合度最高的完整句子。"""
    if not snippet or not full_text:
        return snippet

    # 按句末标点切分 full_text 为完整句子
    sentences = re.split(r'(?<=[。！？!?])', full_text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return snippet

    # 用字级别集合交集计算重合度
    snippet_chars = set(snippet)
    best_sentence = sentences[0]
    best_score = 0

    for sent in sentences:
        sent_chars = set(sent)
        overlap = len(snippet_chars & sent_chars)
        # 用 Jaccard 相似度
        union = len(snippet_chars | sent_chars)
        score = overlap / union if union > 0 else 0
        if score > best_score:
            best_score = score
            best_sentence = sent

    return best_sentence


# 段落匹配

async def _match_one_segment(
    seg: dict,
    points: List[dict],
    model: str,
    semaphore: asyncio.Semaphore,
    course: str = "",
) -> Tuple[Optional[dict], Dict]:
    async with semaphore:
        user_prompt = SEGMENT_MATCH_USER_TEMPLATE.format(
            course=course,
            seg_id=seg["seg_id"],
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
        # 由服务端补齐原始字段
        for ms in data.get("matched_segments", []):
            ms["full_text"] = seg["text"]
            ms["bg"] = seg.get("bg")
            ms["ed"] = seg.get("ed")
            # 将 snippet 对齐到原始完整句子
            if ms.get("text_snippet"):
                ms["text_snippet"] = _realign_snippet(ms["text_snippet"], seg["text"])
        return data, usage
    except Exception as e:
        logger.debug(f"段落匹配解析失败 {seg['seg_id']}: {e}")
        return None, usage


async def match_segments_to_points(
    merged_segments: List[dict],
    points: List[dict],
    model: str,
    concurrency: int = 8,
    course: str = "",
) -> Tuple[List[Optional[dict]], List[Dict]]:
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [
        _match_one_segment(seg, points, model, semaphore, course=course)
        for seg in merged_segments
    ]
    results = await asyncio.gather(*tasks)
    matches = [r[0] for r in results]
    usages = [r[1] for r in results]
    return matches, usages


# 汇总匹配结果

CAT_ORDER = {"basic": 0, "keypoints": 1, "difficulty": 2, "politics": 3}


def _coverage_level(matched: int, total: int) -> str:
    if total == 0:
        return "无"
    ratio = matched / total
    if ratio >= 0.7:
        return "高"
    if ratio >= 0.4:
        return "中"
    return "低"


def _compute_coverage(
    matches: List[Optional[dict]],
    points: List[dict],
    total_segments: int,
) -> dict:
    cat_totals: Dict[str, int] = {}
    cat_matched: Dict[str, set] = {}
    for p in points:
        cat = p["category"]
        cat_totals[cat] = cat_totals.get(cat, 0) + 1
        cat_matched.setdefault(cat, set())

    # 构建 chapter_num 查找表
    point_chapter_map = {
        (p["category"], p["title"]): p.get("chapter_num", 0) for p in points
    }

    matched_point_keys: set = set()
    matched_seg_ids: set = set()

    valid_matches = []
    for m in matches:
        if m is None:
            continue
        key = (m.get("category", ""), m.get("title", ""))
        # 补充 chapter_num
        m["chapter_num"] = point_chapter_map.get(key, 0)
        valid_matches.append(m)
        matched_point_keys.add(key)
        cat = m.get("category", "")
        if cat in cat_matched:
            cat_matched[cat].add(m.get("title", ""))
        for ms in m.get("matched_segments", []):
            matched_seg_ids.add(ms.get("seg_id", ""))

    # 按类别和 seg_id 排序
    valid_matches.sort(key=lambda m: (
        CAT_ORDER.get(m.get("category", ""), 99),
        m.get("matched_segments", [{}])[0].get("seg_id", "") if m.get("matched_segments") else "",
    ))

    # 按固定顺序输出分类覆盖率
    category_coverage = {}
    for cat in ("basic", "keypoints", "difficulty", "politics"):
        total = cat_totals.get(cat, 0)
        matched_count = len(cat_matched.get(cat, set()))
        if total == 0:
            continue
        pct = f"{matched_count / total * 100:.0f}%"
        category_coverage[cat] = {
            "total": total,
            "matched": matched_count,
            "coverage": pct,
            "level": _coverage_level(matched_count, total),
        }

    total_points = len(points)
    matched_points = len(matched_point_keys)
    coverage_pct = f"{matched_points / total_points * 100:.0f}%" if total_points > 0 else "0%"
    matched_segs = len(matched_seg_ids)
    seg_pct = f"{matched_segs / total_segments * 100:.2f}%" if total_segments > 0 else "0%"

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
            "level": _coverage_level(matched_points, total_points),
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
                "chapter_num": p.get("chapter_num", 0),
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


async def _build_alerts(
    course_name: str,
    primary_chapters: List[int],
    overall_coverage: dict,
    category_coverage: dict,
    unmatched_points: List[dict],
    matches: List[Optional[dict]],
    model: str,
) -> Tuple[dict, dict]:
    ch_str = "、".join(str(c) for c in primary_chapters) if primary_chapters else "未知"

    # 统计匹配深度分布
    depth_counter: Dict[str, int] = {}
    for m in matches:
        if m is None:
            continue
        for ms in m.get("matched_segments", []):
            lv = ms.get("match_level", "未知")
            depth_counter[lv] = depth_counter.get(lv, 0) + 1

    user_msg = ALERTS_USER_TEMPLATE.format(
        course_name=course_name,
        chapters=ch_str,
        overall_json=json.dumps(overall_coverage, ensure_ascii=False),
        category_json=json.dumps(category_coverage, ensure_ascii=False),
        unmatched_json=json.dumps(unmatched_points, ensure_ascii=False),
        depth_json=json.dumps(depth_counter, ensure_ascii=False),
    )

    raw, usage = await chat_raw(system_prompt=ALERTS_SYSTEM, user_prompt=user_msg, model=model)
    data = json_repair.loads(raw)

    valid_types = {
        "coverage_insufficient", "keypoints_uncovered",
        "difficulty_uncovered", "ideology_missing", "shallow_teaching",
    }
    alert_types = [t for t in data.get("type", []) if t in valid_types]
    message = data.get("message", "")

    # 后处理：确保 message 中章节号按升序排列
    sorted_ch_str = "、".join(str(c) for c in sorted(primary_chapters)) if primary_chapters else "未知"
    message = re.sub(
        r"第[\d、]+章节",
        f"第{sorted_ch_str}章节",
        message,
    )

    return {"type": alert_types, "message": message}, usage


# 主流程

async def run_lesson_pipeline(
    syllabus_result: dict,
    text_segments: List[dict],
) -> dict:
    """
    课堂语音转写分析主管道。

    Args:
        syllabus_result: 大纲提取结果（含 course + result 字段）
        text_segments: 语音转写段落列表 [{text, bg, ed}, ...]

    Returns:
        分析结果字典
    """
    settings = get_settings()
    model = settings.LLM_MODEL
    course = syllabus_result.get("course", "")
    total_start = time.time()
    all_usages = []

    original_count = len(text_segments)
    logger.info(f"[1/5] 生成知识脑图... 原始段数: {original_count}")

    # 生成知识脑图
    mindmap_result, mindmap_usage = await generate_course_mindmap(
        text_segments, model=model
    )
    all_usages.append(mindmap_usage)

    logger.info("[2/5] 章节匹配...")
    matched_chapters, chapter_usage = await match_chapters(syllabus_result, mindmap_result, model)
    all_usages.append(chapter_usage)

    primary_chapters = sorted(ch.get("num", 0) for ch in matched_chapters if ch.get("num"))
    logger.info(f"匹配章节: {matched_chapters}")

    logger.info("[3/5] 段落合并...")
    merged_segments = merge_text_segments(text_segments)
    logger.info(f"合并后段数: {len(merged_segments)}")

    logger.info("[3c] 提取四要点...")
    points = _extract_points_from_chapters(syllabus_result, matched_chapters)
    logger.info(f"四要点数: {len(points)}")

    logger.info("[3d] 并发段落-知识点匹配...")
    seg_matches, seg_usages = await match_segments_to_points(
        merged_segments, points, model, concurrency=8, course=course
    )
    all_usages.extend(seg_usages)

    logger.info("[4/5] 汇总匹配结果...")
    coverage = _compute_coverage(seg_matches, points, len(merged_segments))
    unmatched = _build_unmatched_points(seg_matches, points)
    summary_text = _build_summary(coverage["overall_coverage"], coverage["category_coverage"])

    logger.info("[5/5] 生成教学预警...")
    alerts, alerts_usage = await _build_alerts(
        course,
        primary_chapters,
        coverage["overall_coverage"],
        coverage["category_coverage"],
        unmatched,
        coverage["matches"],
        model,
    )
    all_usages.append(alerts_usage)

    match_result = {
        "matches": coverage["matches"],
        "unmatched_points": unmatched,
        "category_coverage": coverage["category_coverage"],
        "overall_coverage": coverage["overall_coverage"],
        "summary": summary_text,
        "alerts": alerts,
    }

    logger.info("组装最终输出...")
    total_usage = sum_usage(all_usages)

    result = {
        "primary_chapters": primary_chapters,
        "match_result": match_result,
    }

    elapsed = time.time() - total_start
    logger.info(f"✅ 课堂分析完成，耗时: {elapsed:.2f}s")
    return result
