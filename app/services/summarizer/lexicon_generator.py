"""
词库生成器
为每个知识点的 title 和 summary 生成匹配词库（lexicon）
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.services.models.call_llm import call_llm, extract_json_from_text
from app.prompts.lexicon import SYSTEM_PROMPT_LEXICON
from app.core.config import get_llm_config
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# -------------- 从配置文件加载参数 --------------
llm_config = get_llm_config()
MODEL = llm_config["model"]
API_KEY = llm_config["api_key"]
BASE_URL = llm_config["base_url"]


def _generate_lexicon_for_item(item: dict, chapter_name: str, module_name: str, index: int) -> dict:
    """
    为单个知识点生成词库

    Args:
        item: 包含 title 和 summary 的字典
        chapter_name: 章节名称（用于日志）
        module_name: 模块名称（用于日志）
        index: 索引（用于日志）

    Returns:
        包含 lexicon 的完整字典
    """
    title = item.get("title", "")
    summary = item.get("summary", "")

    # 构建用户提示词
    user_prompt = f"""title: "{title}"
summary: "{summary}"
"""

    try:
        # 调用 LLM
        raw, sub_usage = call_llm(
            model=MODEL,
            user_prompt=user_prompt,
            api_key=API_KEY,
            system_prompt=SYSTEM_PROMPT_LEXICON,
            max_tokens=500,
            temperature=0.3,
            base_url=BASE_URL.strip(),
            return_usage=True
        )

        # 提取 JSON
        data = extract_json_from_text(raw)

        if data and "lexicon" in data:
            lexicon = data["lexicon"]

            # 验证词库格式
            if isinstance(lexicon, list) and len(lexicon) >= 5:
                # 添加 lexicon 到原始 item
                result = item.copy()
                result["lexicon"] = lexicon

                logger.info(f"✅ 生成词库: {chapter_name}/{module_name}[{index}] - {title} ({len(lexicon)}个词条)")
                return result, sub_usage
            else:
                logger.warning(f"⚠️ 词库格式错误: {chapter_name}/{module_name}[{index}] - {title}")
                # 返回原始 item，不添加 lexicon
                return item, sub_usage
        else:
            logger.warning(f"⚠️ 未找到词库: {chapter_name}/{module_name}[{index}] - {title}")
            return item, sub_usage

    except Exception as e:
        logger.error(f"❌ 生成词库失败: {chapter_name}/{module_name}[{index}] - {title} - {e}")
        # 返回原始 item，不添加 lexicon
        return item, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def enrich_keywords_with_lexicon(keywords: list) -> tuple:
    """
    为所有章节的所有知识点生成词库（并发处理）

    Args:
        keywords: 原始的 keywords 列表（章节列表）

    Returns:
        (enriched_keywords, usage): 增强后的 keywords 和 token 使用统计
    """
    logger.info("=" * 60)
    logger.info("开始生成词库（lexicon）...")
    logger.info("=" * 60)

    # 统计信息
    total_items = 0
    for chapter in keywords:
        content = chapter.get("content", {})
        for module_name in ["basic", "key_points", "difficult_points", "politics"]:
            items = content.get(module_name, [])
            total_items += len(items)

    logger.info(f"📊 统计信息：")
    logger.info(f"   - 章节数量: {len(keywords)}")
    logger.info(f"   - 知识点总数: {total_items}")
    logger.info(f"   - 并发策略: 全并发（{total_items} 个任务同时执行）")
    logger.info("=" * 60)

    if total_items == 0:
        logger.info("⚠️ 没有找到需要处理的知识点")
        return keywords, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    # 收集所有任务
    tasks = []  # (chapter_index, module_name, item_index, item, chapter_name)
    for chapter_index, chapter in enumerate(keywords):
        chapter_name = chapter.get("chapter", f"章节{chapter_index+1}")
        content = chapter.get("content", {})

        for module_name in ["basic", "key_points", "difficult_points", "politics"]:
            items = content.get(module_name, [])
            for item_index, item in enumerate(items):
                tasks.append((chapter_index, module_name, item_index, item, chapter_name))

    # 并发生成词库
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    results = {}  # (chapter_index, module_name, item_index) -> enriched_item

    logger.info(f"🚀 启动线程池，最大并发数: {total_items}")

    with ThreadPoolExecutor(max_workers=total_items) as executor:
        # 提交所有任务
        future_map = {}
        for chapter_index, module_name, item_index, item, chapter_name in tasks:
            future = executor.submit(
                _generate_lexicon_for_item,
                item, chapter_name, module_name, item_index
            )
            future_map[future] = (chapter_index, module_name, item_index)

        # 等待所有任务完成
        completed = 0
        failed = 0

        for future in as_completed(future_map):
            chapter_index, module_name, item_index = future_map[future]
            completed += 1

            try:
                enriched_item, sub_usage = future.result()

                # 保存结果
                results[(chapter_index, module_name, item_index)] = enriched_item

                # 累计 token 使用
                usage["prompt_tokens"] += sub_usage.get("prompt_tokens", 0)
                usage["completion_tokens"] += sub_usage.get("completion_tokens", 0)
                usage["total_tokens"] += sub_usage.get("total_tokens", 0)

                logger.info(f"📈 [{completed}/{total_items}] 完成")

            except Exception as e:
                failed += 1
                logger.error(f"📈 [{completed}/{total_items}] ❌ 失败: {e}")

    # 重新组装 keywords
    enriched_keywords = []
    for chapter_index, chapter in enumerate(keywords):
        chapter_name = chapter.get("chapter", f"章节{chapter_index+1}")
        content = chapter.get("content", {})

        enriched_content = {}
        for module_name in ["basic", "key_points", "difficult_points", "politics"]:
            items = content.get(module_name, [])
            enriched_items = []

            for item_index, item in enumerate(items):
                key = (chapter_index, module_name, item_index)
                if key in results:
                    enriched_items.append(results[key])
                else:
                    # 如果没有结果，使用原始 item
                    enriched_items.append(item)

            enriched_content[module_name] = enriched_items

        enriched_keywords.append({
            "chapter": chapter_name,
            "content": enriched_content
        })

    # 打印统计信息
    logger.info("=" * 60)
    logger.info(f"📊 词库生成完成统计：")
    logger.info(f"   - 总知识点数: {total_items}")
    logger.info(f"   - 成功生成: {completed - failed}")
    logger.info(f"   - 失败: {failed}")
    logger.info(f"   - Token使用: {usage['total_tokens']} (输入: {usage['prompt_tokens']}, 输出: {usage['completion_tokens']})")
    logger.info("=" * 60)

    # 去重处理
    logger.info("开始词库去重处理...")
    deduplicated_keywords = deduplicate_lexicons(enriched_keywords)
    logger.info("✅ 词库去重完成")

    return deduplicated_keywords, usage


def deduplicate_lexicons(keywords: list) -> list:
    """
    去除所有章节中重复的词库词条
    如果某个词条在多个位置出现，只保留第一次出现的位置

    Args:
        keywords: 包含词库的 keywords 列表

    Returns:
        去重后的 keywords 列表
    """
    seen_terms = set()  # 记录已经出现过的词条
    duplicate_count = 0  # 统计去重数量

    # 遍历所有章节和模块
    for chapter in keywords:
        content = chapter.get("content", {})

        for module_name in ["basic", "key_points", "difficult_points", "politics"]:
            items = content.get(module_name, [])

            for item in items:
                if "lexicon" not in item:
                    continue

                lexicon = item["lexicon"]
                if not isinstance(lexicon, list):
                    continue

                # 过滤重复词条
                unique_lexicon = []
                for term in lexicon:
                    if term not in seen_terms:
                        unique_lexicon.append(term)
                        seen_terms.add(term)
                    else:
                        duplicate_count += 1
                        logger.debug(f"   去除重复词条: {term}")

                # 更新词库
                item["lexicon"] = unique_lexicon

    logger.info(f"📊 去重统计: 共去除 {duplicate_count} 个重复词条，保留 {len(seen_terms)} 个唯一词条")

    return keywords
