#!/usr/bin/env python3
"""
并发压测脚本
将指定文件夹中的所有文档并发提交给服务端，统计处理结果
"""
import sys
import asyncio
import aiohttp
import base64
import json
import time
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional

# 注入项目根目录
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".pptx", ".ppt"}


async def submit_task(session: aiohttp.ClientSession, server_url: str, file_path: Path) -> Optional[str]:
    """提交单个文档，返回 task_id"""
    file_bytes = file_path.read_bytes()
    filedata = base64.b64encode(file_bytes).decode("utf-8")
    payload = {"filedata": filedata, "filename": file_path.name}

    try:
        async with session.post(
            f"{server_url}/api/v1/document/process",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            if resp.status == 202:
                data = await resp.json()
                task_id = data["task_id"]
                print(f"  [提交成功] {file_path.name} -> {task_id}")
                return task_id
            elif resp.status == 429:
                print(f"  [队列已满] {file_path.name} -> 429 服务繁忙")
                return None
            else:
                text = await resp.text()
                print(f"  [提交失败] {file_path.name} -> HTTP {resp.status}: {text[:100]}")
                return None
    except Exception as e:
        print(f"  [提交异常] {file_path.name} -> {e}")
        return None


async def poll_task(
    session: aiohttp.ClientSession,
    server_url: str,
    task_id: str,
    filename: str,
    poll_interval: int,
    max_wait: int
) -> dict:
    """轮询单个任务直到完成或超时，返回结果摘要"""
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed > max_wait:
            return {"task_id": task_id, "filename": filename, "status": "timeout", "elapsed": elapsed}

        try:
            async with session.get(
                f"{server_url}/api/v1/document/status/{task_id}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    await asyncio.sleep(poll_interval)
                    continue
                data = await resp.json()
        except Exception:
            await asyncio.sleep(poll_interval)
            continue

        status = data.get("status")
        if status == "completed":
            return {"task_id": task_id, "filename": filename, "status": "completed",
                    "elapsed": elapsed, "data": data}
        elif status == "failed":
            return {"task_id": task_id, "filename": filename, "status": "failed",
                    "elapsed": elapsed, "error": data.get("error", "")}
        else:
            await asyncio.sleep(poll_interval)


async def process_file(
    session: aiohttp.ClientSession,
    server_url: str,
    file_path: Path,
    poll_interval: int,
    max_wait: int,
    results_dir: Path
) -> dict:
    """提交 + 轮询单个文件的完整流程"""
    task_id = await submit_task(session, server_url, file_path)
    if not task_id:
        return {"filename": file_path.name, "status": "submit_failed"}

    result = await poll_task(session, server_url, task_id, file_path.name, poll_interval, max_wait)

    if result["status"] == "completed":
        out_file = results_dir / f"{task_id}.json"
        out_file.write_text(
            json.dumps(result["data"], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"  [完成] {file_path.name} 耗时 {result['elapsed']:.1f}s -> {out_file.name}")
    elif result["status"] == "failed":
        print(f"  [失败] {file_path.name} 耗时 {result['elapsed']:.1f}s 错误: {result.get('error','')}")
    else:
        print(f"  [超时] {file_path.name} 超过 {max_wait}s 未完成")

    return result


def print_summary(results: list, total_elapsed: float):
    print("\n" + "=" * 60)
    print("并发测试结果汇总")
    print("=" * 60)

    completed = [r for r in results if r["status"] == "completed"]
    failed = [r for r in results if r["status"] == "failed"]
    timeout = [r for r in results if r["status"] == "timeout"]
    submit_failed = [r for r in results if r["status"] == "submit_failed"]

    print(f"  总文件数:   {len(results)}")
    print(f"  成功完成:   {len(completed)}")
    print(f"  处理失败:   {len(failed)}")
    print(f"  等待超时:   {len(timeout)}")
    print(f"  提交失败:   {len(submit_failed)}")
    print(f"  总耗时:     {total_elapsed:.1f}s")

    if completed:
        avg = sum(r["elapsed"] for r in completed) / len(completed)
        print(f"  平均处理时间: {avg:.1f}s")

    if failed:
        print("\n失败详情:")
        for r in failed:
            print(f"  - {r['filename']}: {r.get('error','')}")

    print("=" * 60)


async def main():
    parser = argparse.ArgumentParser(description="并发文档处理压测脚本")
    parser.add_argument("--folder", default="/root/workspace/教学大纲四要点核心内容提取工程/app/tests/data/pdf20" ,help="包含测试文档的文件夹路径")
    parser.add_argument("--server", default="http://localhost:5000", help="服务器地址")
    parser.add_argument("--interval", type=int, default=20, help="轮询间隔秒数（默认: 20）")
    parser.add_argument("--timeout", type=int, default=600, help="单任务最大等待秒数（默认: 600）")
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"错误: {folder} 不是有效目录")
        sys.exit(1)

    files = [f for f in folder.iterdir() if f.suffix.lower() in SUPPORTED_EXTENSIONS]
    if not files:
        print(f"未找到支持的文档文件（{', '.join(SUPPORTED_EXTENSIONS)}）")
        sys.exit(1)

    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)

    print(f"服务器: {args.server}")
    print(f"文件夹: {folder}")
    print(f"文件数: {len(files)}")
    print(f"并发数: {len(files)}（全部同时提交）")
    print(f"结果目录: {results_dir}")
    print("=" * 60)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始并发提交...")

    total_start = time.time()

    connector = aiohttp.TCPConnector(limit=100)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            process_file(session, args.server, f, args.interval, args.timeout, results_dir)
            for f in files
        ]
        results = await asyncio.gather(*tasks)

    print_summary(results, time.time() - total_start)


if __name__ == "__main__":
    asyncio.run(main())

# 使用方式 调整上方参数
# 终端运行
# python app/tests/test_concurrency.py 
