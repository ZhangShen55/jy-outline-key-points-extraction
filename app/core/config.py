"""
配置管理模块
"""
import tomllib
from pathlib import Path
from pydantic_settings import BaseSettings
from functools import lru_cache


def load_toml_config() -> dict:
    """加载 config.toml 配置文件"""
    config_path = Path("config.toml")
    if config_path.exists():
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    return {}


_toml = load_toml_config()


class Settings(BaseSettings):
    """应用配置"""

    # 项目信息
    PROJECT_NAME: str = "教学大纲四要点核心内容提取系统"
    VERSION: str = "1.0.0"
    DEBUG: bool = _toml.get("project", {}).get("debug", False)

    # LLM 配置（从 .env 读取）
    LLM_MODEL: str = "doubao-seed-2-0-pro-260215"
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = ""
    LLM_MAX_TOKENS: int = 8192
    LLM_TEMPERATURE: float = 0.0

    # Dolphin OCR 模型路径
    PARSER_MODEL_PATH: str = ""

    # 分块配置
    CHUNK_SIZE: int = _toml.get("chunking", {}).get("chunk_size", 10000)
    CHUNK_OVERLAP: int = _toml.get("chunking", {}).get("overlap", 1000)
    BATCH_SIZE: int = _toml.get("chunking", {}).get("batch_size", 100)

    # 并发控制
    MAX_CONCURRENT: int = _toml.get("concurrency", {}).get("max_concurrent", 10)
    MAX_QUEUE: int = _toml.get("concurrency", {}).get("max_queue", 10)

    # 日志配置
    LOG_LEVEL: str = _toml.get("logging", {}).get("level", "INFO")
    LOG_FILE: str = _toml.get("logging", {}).get("file", "app.log")
    LOG_FORMAT: str = _toml.get("logging", {}).get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # GPU 配置
    CUDA_VISIBLE_DEVICES: str = "1"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """返回配置单例"""
    return Settings()


def get_llm_config() -> dict:
    """返回 LLM 配置字典"""
    settings = get_settings()
    return {
        "model": settings.LLM_MODEL,
        "api_key": settings.LLM_API_KEY,
        "base_url": settings.LLM_BASE_URL,
        "max_tokens": settings.LLM_MAX_TOKENS,
        "temperature": settings.LLM_TEMPERATURE,
    }


def get_parser_model_path() -> str:
    """返回 Dolphin OCR 模型路径"""
    return get_settings().PARSER_MODEL_PATH


def get_chunking_config() -> dict:
    """返回分块配置字典"""
    settings = get_settings()
    return {
        "chunk_size": settings.CHUNK_SIZE,
        "overlap": settings.CHUNK_OVERLAP,
        "batch_size": settings.BATCH_SIZE,
    }
