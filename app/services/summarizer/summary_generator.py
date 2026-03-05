import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.services.models.call_llm import call_llm, extract_json_from_text
from app.prompts.dagang import SYSTEM_PROMPT_req, SYSTEM_PROMPT_imp, SYSTEM_PROMPT_diffi, SYSTEM_PROMPT_poli
from app.core.config import get_llm_config
from app.core.logging_config import get_logger
logger = get_logger(__name__)

# -------------- 从配置文件加载参数 --------------
llm_config = get_llm_config()
MODEL = llm_config["model"]
API_KEY = llm_config["api_key"]
BASE_URL = llm_config["base_url"]
MAX_TOKENS = llm_config["max_tokens"]
TEMPERATURE = llm_config["temperature"]


def _call_llm_for_module(txt_file, prompt):
    """单个模块的 LLM 调用任务，供线程池并发执行。"""
    json_file = txt_file.with_suffix(".json")

    # 已存在则跳过调用
    if json_file.exists():
        logger.info(f"⏭️ 已存在，跳过：{json_file}")
        return json_file, None

    text = txt_file.read_text(encoding="utf-8")
    raw, sub_usage = call_llm(
        model=MODEL,
        user_prompt=text,
        api_key=API_KEY,
        system_prompt=prompt,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        base_url=BASE_URL.strip(),
        return_usage=True
    )
    data = extract_json_from_text(raw)
    if data is None:
        raise ValueError("返回内容不含合法 JSON")
    json_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"✅ 生成：{json_file}")
    return json_file, sub_usage


# -------------- 智能摘要化 --------------
def extract_all_modules(out_dir):
    """
    遍历 chapters 下所有章节：
    - 对每章的 4 个模块（基本要求、教学重点、教学难点、课程思政）并发执行摘要生成；
    - 将结果以 { "chapter": 章节名, "content": {...} } 结构返回；
    - 所有章节拼接成一个总 JSON 并返回。
    """
    logger.info("开始智能摘要化（生成/补全各模块 .json）……")
    logger.info("=" * 60)
    root = out_dir / "chapters"
    # 文件名到模块key的映射
    FILE_TO_MODULE = {
        "2_基本要求.txt": "basic",
        "3_教学重点.txt": "key_points",
        "4_教学难点.txt": "difficult_points",
        "6_课程思政.txt": "politics",
    }

    PROMPT_MAP = {
        "2_基本要求.txt": SYSTEM_PROMPT_req,
        "3_教学重点.txt": SYSTEM_PROMPT_imp,
        "4_教学难点.txt": SYSTEM_PROMPT_diffi,
        "6_课程思政.txt": SYSTEM_PROMPT_poli,
    }

    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    # ---- 第一步：收集所有需要处理的任务 ----
    chapter_dirs = sorted(d for d in root.glob("*") if d.is_dir())
    tasks = []  # (chapter_dir, txt_file, key)
    for chapter_dir in chapter_dirs:
        for txt_file in chapter_dir.glob("*.txt"):
            if txt_file.name in PROMPT_MAP:
                tasks.append((chapter_dir, txt_file, txt_file.name))

    logger.info(f"共 {len(chapter_dirs)} 个章节，{len(tasks)} 个模块任务，开始并发调用 LLM...")

    # ---- 第二步：并发提交所有 LLM 调用 ----
    future_map = {}  # future -> (chapter_dir, key)
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        for chapter_dir, txt_file, key in tasks:
            future = executor.submit(_call_llm_for_module, txt_file, PROMPT_MAP[key])
            future_map[future] = (chapter_dir, key)

        # 等待所有任务完成，实时打印进度
        for future in as_completed(future_map):
            chapter_dir, key = future_map[future]
            try:
                _, sub_usage = future.result()
                if sub_usage:
                    usage["prompt_tokens"] += sub_usage.get("prompt_tokens", 0)
                    usage["completion_tokens"] += sub_usage.get("completion_tokens", 0)
                    usage["total_tokens"] += sub_usage.get("total_tokens", 0)
            except Exception as e:
                logger.info(f"❌ 处理 {chapter_dir.name}/{key} 失败: {e}")

    # ---- 第三步：读取所有 JSON 结果，按章节组装 ----
    all_results = []
    for chapter_dir in chapter_dirs:
        chapter_name = chapter_dir.name.strip()
        chapter_result = {"chapter": chapter_name, "content": {}}

        for txt_file in chapter_dir.glob("*.txt"):
            key = txt_file.name
            if key not in PROMPT_MAP:
                continue
            json_file = txt_file.with_suffix(".json")
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # module_name = key.replace(".txt", "")
                module_name = FILE_TO_MODULE[key]
                chapter_result["content"][module_name] = data
            except Exception as e:
                logger.info(f"⚠️ 无法读取 {json_file}: {e}")

        if chapter_result["content"]:
            all_results.append(chapter_result)

    logger.info("✅ 全部章节摘要生成并拼接完成！")
    logger.info("=" * 60)

    # ---- 第四步：为所有知识点生成词库（lexicon）----
    from app.services.summarizer.lexicon_generator import enrich_keywords_with_lexicon

    logger.info("\n开始为知识点生成词库...")
    enriched_results, lexicon_usage = enrich_keywords_with_lexicon(all_results)

    # 合并 token 使用统计
    usage["prompt_tokens"] += lexicon_usage.get("prompt_tokens", 0)
    usage["completion_tokens"] += lexicon_usage.get("completion_tokens", 0)
    usage["total_tokens"] += lexicon_usage.get("total_tokens", 0)

    logger.info("✅ 词库生成完成！")

    return enriched_results, usage
