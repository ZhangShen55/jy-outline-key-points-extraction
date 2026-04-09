"""配置管理。"""
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

    # LLM 配置（密钥从 .env，其他从 config.toml）
    LLM_MODEL: str = _toml.get("llm", {}).get("model", "doubao-seed-2-0-pro-260215")
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = _toml.get("llm", {}).get("base_url", "")
    LLM_MAX_TOKENS: int = _toml.get("llm", {}).get("max_tokens", 8192)
    LLM_TEMPERATURE: float = _toml.get("llm", {}).get("temperature", 0.0)

    # Dolphin OCR 模型路径
    PARSER_MODEL_PATH: str = ""

    # 分块配置
    CHUNK_SIZE: int = _toml.get("chunking", {}).get("chunk_size", 10000)
    CHUNK_OVERLAP: int = _toml.get("chunking", {}).get("overlap", 1000)
    BATCH_SIZE: int = _toml.get("chunking", {}).get("batch_size", 100)

    # 并发控制
    MAX_CONCURRENT: int = _toml.get("concurrency", {}).get("max_concurrent", 10)
    MAX_QUEUE: int = _toml.get("concurrency", {}).get("max_queue", 10)

    # 数据库配置
    DB_HOST: str = _toml.get("database", {}).get("host", "localhost")
    DB_PORT: int = _toml.get("database", {}).get("port", 54320)
    DB_USER: str = _toml.get("database", {}).get("user", "postgres")
    DB_PASSWORD: str = _toml.get("database", {}).get("password", "")
    DB_NAME: str = _toml.get("database", {}).get("name", "syllabus_db")
    DB_POOL_SIZE: int = _toml.get("database", {}).get("pool_size", 100)
    DB_MAX_OVERFLOW: int = _toml.get("database", {}).get("max_overflow", 50)
    DB_POOL_TIMEOUT: int = _toml.get("database", {}).get("pool_timeout", 30)

    # 质量画像数据库
    QUALITY_DB_HOST: str = _toml.get("quality_database", {}).get("host", _toml.get("database", {}).get("host", "localhost"))
    QUALITY_DB_PORT: int = _toml.get("quality_database", {}).get("port", _toml.get("database", {}).get("port", 54320))
    QUALITY_DB_USER: str = _toml.get("quality_database", {}).get("user", _toml.get("database", {}).get("user", "postgres"))
    QUALITY_DB_PASSWORD: str = _toml.get("quality_database", {}).get("password", _toml.get("database", {}).get("password", ""))
    QUALITY_DB_NAME: str = _toml.get("quality_database", {}).get("name", _toml.get("database", {}).get("name", "syllabus_db"))
    QUALITY_DB_POOL_SIZE: int = _toml.get("quality_database", {}).get("pool_size", _toml.get("database", {}).get("pool_size", 100))
    QUALITY_DB_MAX_OVERFLOW: int = _toml.get("quality_database", {}).get("max_overflow", _toml.get("database", {}).get("max_overflow", 50))
    QUALITY_DB_POOL_TIMEOUT: int = _toml.get("quality_database", {}).get("pool_timeout", _toml.get("database", {}).get("pool_timeout", 30))

    # 日志配置
    LOG_LEVEL: str = _toml.get("logging", {}).get("level", "INFO")
    LOG_FILE: str = _toml.get("logging", {}).get("file", "app.log")
    LOG_FORMAT: str = _toml.get("logging", {}).get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # GPU 配置
    CUDA_VISIBLE_DEVICES: str = "1"

    # Embedding 配置
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MODEL: str = _toml.get("embedding", {}).get("model", "BAAI/bge-m3")
    EMBEDDING_BASE_URL: str = _toml.get("embedding", {}).get("base_url", "https://api.siliconflow.cn/v1")
    EMBEDDING_DIMENSION: int = _toml.get("embedding", {}).get("dimension", 1024)
    EMBEDDING_BATCH_SIZE: int = _toml.get("embedding", {}).get("batch_size", 32)

    # Rerank 配置
    RERANK_API_KEY: str = ""
    RERANK_ENABLED: bool = _toml.get("rerank", {}).get("enabled", True)
    RERANK_MODEL: str = _toml.get("rerank", {}).get("model", "BAAI/bge-reranker-v2-m3")
    RERANK_BASE_URL: str = _toml.get("rerank", {}).get("base_url", "https://api.siliconflow.cn/v1")
    RERANK_THRESHOLD: int = _toml.get("rerank", {}).get("threshold_for_rerank", 50)

    # 词库匹配配置
    MATCH_DEFAULT_MIN_SCORE: float = _toml.get("lexicon_match", {}).get("default_min_score", 0.5)
    MATCH_MAX_TOP: int = _toml.get("lexicon_match", {}).get("max_top", 20)

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
