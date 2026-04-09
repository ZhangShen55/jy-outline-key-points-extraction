"""质量画像数据联调脚本。

用途：
1. 将上游原始 ASR/OCR JSON 转换为 /quality/courses/data-ingestion 请求体；
2. 可选直接调用接口做端到端联调。
"""

from __future__ import annotations

import argparse
import json
import random
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

import requests

from app.schemas.quality import QualityDataIngestionRequest


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _extract_asr_list(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            if isinstance(data.get("afterAssemblyList"), list):
                return [x for x in data.get("afterAssemblyList", []) if isinstance(x, dict)]
            if isinstance(data.get("beforeAssemblyList"), list):
                return [x for x in data.get("beforeAssemblyList", []) if isinstance(x, dict)]
    return []


def _extract_ocr_list(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            if isinstance(data.get("docList"), list):
                return [x for x in data.get("docList", []) if isinstance(x, dict)]
    return []


def _normalize_asr(raw_asr: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """映射规则（按用户说明）：
    - bg/ed 原始为毫秒，转换为秒；
    - res -> text；
    - role 固定 teacher；
    - emotion 固定 平淡；
    - speed 在 120/200 间随机。
    """
    rng = random.Random(20260408)
    normalized: List[Dict[str, Any]] = []
    for item in raw_asr:
        text = str(item.get("res", "")).strip()
        if not text:
            continue
        bg_ms = _to_float(item.get("bg"), 0.0)
        ed_ms = _to_float(item.get("ed"), bg_ms)
        if ed_ms < bg_ms:
            bg_ms, ed_ms = ed_ms, bg_ms
        normalized.append(
            {
                "bg": round(bg_ms / 1000.0, 3),
                "ed": round(ed_ms / 1000.0, 3),
                "role": "teacher",
                "text": text,
                "emotion": "平淡",
                "speed": rng.choice([120, 200]),
            }
        )
    return normalized


def _normalize_ocr(raw_ocr: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """映射规则（按用户说明）：
    - imageSeekTime -> time_offset（秒）；
    - ocrText -> ocr_content；
    - page_num 按 imageId 升序后从 1 开始重排。
    """
    sorted_docs = sorted(raw_ocr, key=lambda x: _to_int(x.get("imageId"), 0))
    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(sorted_docs, start=1):
        content = str(item.get("ocrText", "")).strip()
        if not content:
            continue
        normalized.append(
            {
                "time_offset": max(0, _to_int(item.get("imageSeekTime"), 0)),
                "page_num": idx,
                "ocr_content": content,
            }
        )
    return normalized


def build_ingestion_payload(
    asr_file: Path,
    ocr_file: Path,
    *,
    course_id: str,
    lesson_id: str,
    week_number: int,
    lesson_index_in_week: int,
    lesson_index_global: int,
) -> Dict[str, Any]:
    asr_raw = json.loads(asr_file.read_text(encoding="utf-8"))
    ocr_raw = json.loads(ocr_file.read_text(encoding="utf-8"))
    asr_data = _normalize_asr(_extract_asr_list(asr_raw))
    ocr_data = _normalize_ocr(_extract_ocr_list(ocr_raw))

    payload = {
        "course_id": course_id,
        "course_name": "综合岩石学",
        "academic_year": "25-26-2",
        "teacher": None,
        "total_weeks": 16,
        "total_lessons": 32,
        "lesson_id": lesson_id,
        "week_number": week_number,
        "lesson_index_in_week": lesson_index_in_week,
        "lesson_index_global": lesson_index_global,
        "avg_head_up_rate": 0.25,
        "asr_data": asr_data,
        "ocr_data": ocr_data,
    }

    # Pydantic 校验，确保请求体符合接口约束
    validated = QualityDataIngestionRequest(**payload)
    return validated.model_dump()


def _post(url: str, body: Dict[str, Any]) -> requests.Response:
    return requests.post(url, json=body, timeout=120)


def run_chain(server_url: str, payload: Dict[str, Any], timeout_sec: int = 120) -> None:
    base = server_url.rstrip("/")
    ingest_url = f"{base}/api/v1/quality/courses/data-ingestion"
    generate_url = f"{base}/api/v1/quality/tasks/semester-profile/generate"
    status_url = f"{base}/api/v1/quality/tasks/semester-profile/status/query"
    module_url = f"{base}/api/v1/quality/courses/semester-profile/module/query"

    print(f"\n[1/4] POST {ingest_url}")
    try:
        ingest_resp = _post(ingest_url, payload)
    except requests.RequestException as e:
        print(f"请求失败：{e}")
        print("请先启动服务（以及数据库）后重试。")
        return
    print("status:", ingest_resp.status_code)
    print("body:", ingest_resp.text[:1200])
    if ingest_resp.status_code >= 500:
        return

    print(f"\n[2/4] POST {generate_url}")
    gen_body = {
        "course_id": payload["course_id"],
        "target_week": None,
        "force": False,
    }
    try:
        generate_resp = _post(generate_url, gen_body)
    except requests.RequestException as e:
        print(f"请求失败：{e}")
        return
    print("status:", generate_resp.status_code)
    print("body:", generate_resp.text[:1200])
    if generate_resp.status_code != 202:
        return

    task_id = generate_resp.json().get("data", {}).get("task_id")
    if not task_id:
        return

    print(f"\n[3/4] 轮询任务状态 task_id={task_id}")
    deadline = time.time() + timeout_sec
    final_status = None
    while time.time() < deadline:
        try:
            r = _post(status_url, {"task_id": task_id})
        except requests.RequestException as e:
            print(f"status 查询请求失败：{e}")
            time.sleep(2)
            continue
        if r.status_code != 200:
            print("status query http:", r.status_code, r.text[:500])
            time.sleep(2)
            continue
        body = r.json()
        data = body.get("data", {})
        status = data.get("status")
        current_node = data.get("current_node")
        progress = data.get("progress_pct")
        print(f"status={status}, node={current_node}, progress={progress}%")
        if status in (2, 3, 4):
            final_status = status
            break
        time.sleep(2)

    if final_status is None:
        print("任务轮询超时")
        return

    print(f"\n[4/4] POST {module_url}")
    module_body = {
        "course_id": payload["course_id"],
        "report_level": "semester",
        "target_identifier": payload["course_id"],
        "module_name": "radar",
    }
    try:
        module_resp = _post(module_url, module_body)
    except requests.RequestException as e:
        print(f"请求失败：{e}")
        return
    print("status:", module_resp.status_code)
    print("body:", module_resp.text[:1200])


def main() -> None:
    parser = argparse.ArgumentParser(description="质量画像原始数据转换与联调脚本")
    parser.add_argument(
        "--asr-file",
        default="app/tests/quality-data/weekend1-lesson1-asr.json",
        help="原始 ASR 文件路径",
    )
    parser.add_argument(
        "--ocr-file",
        default="app/tests/quality-data/weekend1-lesson1-ocr.json",
        help="原始 OCR 文件路径",
    )
    parser.add_argument(
        "--output",
        default="app/tests/quality-data/weekend1-lesson1-ingestion-request.json",
        help="转换后请求体输出路径",
    )
    parser.add_argument("--course-id", default=str(uuid.uuid5(uuid.NAMESPACE_DNS, "综合岩石学:25-26-2")))
    parser.add_argument("--lesson-id", default="week1-lesson1")
    parser.add_argument("--week-number", type=int, default=1)
    parser.add_argument("--lesson-index-in-week", type=int, default=1)
    parser.add_argument("--lesson-index-global", type=int, default=1)
    parser.add_argument("--submit", action="store_true", help="是否直接提交到服务端")
    parser.add_argument("--server-url", default="http://localhost:8000")
    parser.add_argument("--timeout-sec", type=int, default=120)
    args = parser.parse_args()

    asr_file = Path(args.asr_file)
    ocr_file = Path(args.ocr_file)
    if not asr_file.exists():
        raise FileNotFoundError(f"ASR 文件不存在: {asr_file}")
    if not ocr_file.exists():
        raise FileNotFoundError(f"OCR 文件不存在: {ocr_file}")

    payload = build_ingestion_payload(
        asr_file,
        ocr_file,
        course_id=args.course_id,
        lesson_id=args.lesson_id,
        week_number=args.week_number,
        lesson_index_in_week=args.lesson_index_in_week,
        lesson_index_global=args.lesson_index_global,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("转换完成:")
    print(f"- 输出文件: {output}")
    print(f"- course_id: {payload['course_id']}")
    print(f"- lesson_id: {payload['lesson_id']}")
    print(f"- asr_data: {len(payload['asr_data'])} 条")
    print(f"- ocr_data: {len(payload['ocr_data'])} 条")

    if args.submit:
        run_chain(args.server_url, payload, timeout_sec=args.timeout_sec)


if __name__ == "__main__":
    main()
