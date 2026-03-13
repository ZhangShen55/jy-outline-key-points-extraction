"""
脑图生成测试脚本

使用 converted_石油与天然气地质学_2025-12-05_第3节课.json.json 的转写内容，
调用 generate_course_mindmap 生成脑图，验证输出结构是否与 脑图结果示例.json 一致。

用法：
    python app/tests/test_mindmap.py
"""

import asyncio
import json
import sys
import time
from pathlib import Path

# 项目根目录加入 sys.path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def validate_node(node: dict, depth: int = 0, prefix: str = "") -> list:
    """递归校验节点结构，返回错误列表。"""
    errors = []
    for field in ("id", "label", "time"):
        if field not in node:
            errors.append(f"{prefix}depth={depth} 缺少字段 {field}")

    time_str = node.get("time", "")
    if time_str:
        parts = time_str.split("-")
        if len(parts) == 2:
            try:
                s, e = int(parts[0]), int(parts[1])
                if s > e:
                    errors.append(f"{prefix}id={node.get('id')} time 反转: {time_str}")
            except ValueError:
                errors.append(f"{prefix}id={node.get('id')} time 非整数: {time_str}")
        else:
            errors.append(f"{prefix}id={node.get('id')} time 格式异常: {time_str}")

    children = node.get("children")
    if children:
        for i, child in enumerate(children):
            errors.extend(validate_node(child, depth + 1, prefix=f"  "))
    return errors


def validate_mindmap_result(result: dict) -> list:
    """校验 generate_course_mindmap 的输出结构，返回错误列表。"""
    errors = []

    # 顶层字段
    for key in ("full_overview", "key_points", "document_skims", "mindmap"):
        if key not in result:
            errors.append(f"缺少顶层字段: {key}")

    # full_overview
    fo = result.get("full_overview", "")
    if not isinstance(fo, str) or len(fo) < 20:
        errors.append(f"full_overview 过短或类型错误: {type(fo)} len={len(fo) if isinstance(fo, str) else 'N/A'}")

    # key_points
    kp = result.get("key_points", [])
    if not isinstance(kp, list) or len(kp) == 0:
        errors.append(f"key_points 应为非空列表: {type(kp)}")

    # document_skims
    ds = result.get("document_skims", [])
    if not isinstance(ds, list) or len(ds) == 0:
        errors.append("document_skims 应为非空列表")
    else:
        for i, skim in enumerate(ds):
            for field in ("time", "overview", "content"):
                if field not in skim:
                    errors.append(f"document_skims[{i}] 缺少字段: {field}")

    # mindmap
    mm = result.get("mindmap", {})
    for field in ("overall_label", "total_time", "nodes"):
        if field not in mm:
            errors.append(f"mindmap 缺少字段: {field}")

    nodes = mm.get("nodes", [])
    if not isinstance(nodes, list) or len(nodes) == 0:
        errors.append("mindmap.nodes 应为非空列表")
    else:
        for i, node in enumerate(nodes):
            node_errors = validate_node(node)
            errors.extend(node_errors)
            # 检查三层结构：父 -> 子(3) -> 孙(3)
            children = node.get("children", [])
            if len(children) != 3:
                errors.append(f"nodes[{i}] 子节点数={len(children)}，期望 3")
            for j, child in enumerate(children):
                grandchildren = child.get("children", [])
                if len(grandchildren) != 3:
                    errors.append(f"nodes[{i}].children[{j}] 孙节点数={len(grandchildren)}，期望 3")

    return errors


async def main():
    # 加载转写数据
    input_file = ROOT / "converted_石油与天然气地质学_2025-12-05_第3节课.json.json"
    if not input_file.exists():
        print(f"❌ 输入文件不存在: {input_file}")
        sys.exit(1)

    with open(input_file, encoding="utf-8") as f:
        data = json.load(f)
    segments = data["textSegments"]
    print(f"📄 加载转写数据: {len(segments)} 条 segments")

    # 调用脑图生成
    from app.services.mindmap_generator import generate_course_mindmap

    print(f"🚀 开始生成脑图 (model=doubao-seed-2-0-pro-260215)...")
    t0 = time.time()

    try:
        result, usage = await generate_course_mindmap(
            segments,
            model="doubao-seed-2-0-pro-260215",
            concurrency=4,
            max_rounds=5,
        )
    except Exception as e:
        print(f"❌ 生成失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    elapsed = time.time() - t0
    print(f"✅ 生成完成，耗时: {elapsed:.1f}s")
    print(f"📊 Token 用量: {usage}")

    # 结构校验
    print("\n--- 结构校验 ---")
    errors = validate_mindmap_result(result)
    if errors:
        print(f"⚠️  发现 {len(errors)} 个问题:")
        for err in errors:
            print(f"  - {err}")
    else:
        print("✅ 结构校验全部通过")

    # 打印关键信息
    print(f"\n--- 结果摘要 ---")
    print(f"full_overview: {result.get('full_overview', '')[:80]}...")
    print(f"key_points ({len(result.get('key_points', []))}): {result.get('key_points', [])}")
    print(f"document_skims: {len(result.get('document_skims', []))} 段")
    mm = result.get("mindmap", {})
    print(f"overall_label: {mm.get('overall_label', '')}")
    print(f"total_time: {mm.get('total_time', '')}")
    print(f"nodes: {len(mm.get('nodes', []))} 个父节点")

    # 保存结果
    output_path = ROOT / "app" / "tests" / "results" / "test_mindmap_result.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "model": "doubao-seed-2-0-pro-260215",
        "result": {
            "overview": result,
            "process_time_ms": int(elapsed * 1000),
            "finished_reason": "stop",
        },
        "usage": usage,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n💾 结果已保存: {output_path}")

    # 与参考文件对比结构
    ref_file = ROOT / "脑图结果示例.json"
    if ref_file.exists():
        with open(ref_file, encoding="utf-8") as f:
            ref = json.load(f)
        ref_overview = ref.get("result", {}).get("overview", {})
        print(f"\n--- 与参考文件对比 ---")
        print(f"参考 key_points 数: {len(ref_overview.get('key_points', []))}, 实际: {len(result.get('key_points', []))}")
        print(f"参考 document_skims 数: {len(ref_overview.get('document_skims', []))}, 实际: {len(result.get('document_skims', []))}")
        ref_nodes = ref_overview.get("mindmap", {}).get("nodes", [])
        act_nodes = mm.get("nodes", [])
        print(f"参考 nodes 数: {len(ref_nodes)}, 实际: {len(act_nodes)}")

    sys.exit(0 if not errors else 1)


if __name__ == "__main__":
    asyncio.run(main())
