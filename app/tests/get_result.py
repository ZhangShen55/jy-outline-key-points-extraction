"""
获取任务结果并保存到 app/tests/results/
用法: python app/tests/get_result.py <task_id>
"""
import sys
import json
import requests
from pathlib import Path


SERVER_URL = "http://localhost:8000"
RESULTS_DIR = Path(__file__).parent / "results"


def get_result(task_id: str):
    RESULTS_DIR.mkdir(exist_ok=True)

    resp = requests.get(f"{SERVER_URL}/api/v1/document/status/{task_id}", timeout=10)

    if resp.status_code == 404:
        print(f"❌ 任务不存在: {task_id}")
        sys.exit(1)

    data = resp.json()
    status = data.get("status")

    if status != "completed":
        print(f"⚠️  任务状态: {status} - {data.get('message', '')}")
        sys.exit(1)

    # 保存完整响应
    out = RESULTS_DIR / f"{task_id}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 已保存到: {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python app/tests/get_result.py <task_id>")
        sys.exit(1)

    get_result(sys.argv[1])
