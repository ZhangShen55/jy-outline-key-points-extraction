
"""
完整流程：文档解析 -> 标题提取 -> 知识点分割
"""
import json
import os
import re
import requests
import time
import asyncio
from pathlib import Path
import tempfile
import subprocess
import logging
import io
import base64
import cv2
import torch
from PIL import Image
from transformers import AutoProcessor, VisionEncoderDecoderModel
import concurrent.futures
import torch.multiprocessing as mp
import argparse
import glob
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from utils.utils import *
import argparse, asyncio
from app.services.parsers.document_parser import parse_document_to_text
from app.services.parsers.chapter_splitter import extract_chapters_by_traditional_method
from app.services.parsers.subpoint_splitter import split_subpoints
from app.services.summarizer.summary_generator import extract_all_modules
from app.core.logging_config import get_logger
logger = get_logger(__name__)

# 设置环境变量
os.environ["CUDA_VISIBLE_DEVICES"] = "2"

# 配置参数

DOLPHIN_MODEL_PATH = r"/data1/vllm/wyx/Dolphin_project/hf_model/Dolphin"
VLM_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "output_all"

API_KEY = "sk-grvrkqeommrueqrcbddawvkqujbzyhkpvslyunxpetpedwlm"
BASE_URL = "https://api.siliconflow.cn/v1/chat/completions"
MODEL = "Qwen/QwQ-32B"

async def main():
    parser = argparse.ArgumentParser(description="教学大纲处理流水线")
    parser.add_argument("input", help="输入文件路径")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR, help="输出目录")
    args = parser.parse_args()

    logger.info(f"开始解析: {args.input}")
    text_file = await parse_document_to_text(args.input, DOLPHIN_MODEL_PATH)
    full_text = text_file.read_text(encoding="utf-8")

    logger.info("章节切割中……")
    extract_chapters_by_traditional_method(full_text, args.output)

    logger.info("二级要点切割中……")
    split_subpoints(args.output)

    logger.info("调用 LLM 摘要生成中……")
    extract_all_modules(args.output)

    logger.info(f"✅ 全部完成，结果保存在: {args.output}")

if __name__ == "__main__":
    asyncio.run(main())