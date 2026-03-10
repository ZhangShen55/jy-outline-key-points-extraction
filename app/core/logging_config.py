"""
日志配置模块
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from app.core.config import get_settings


def setup_logging():
    """初始化日志系统"""
    settings = get_settings()

    logger = logging.getLogger()
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    logger.handlers.clear()

    formatter = logging.Formatter(
        settings.LOG_FORMAT,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件输出：单文件 10MB，保留 5 个轮转备份
    if settings.LOG_FILE:
        file_handler = RotatingFileHandler(
            settings.LOG_FILE,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """返回指定名称的日志记录器"""
    return logging.getLogger(name)


def config_logger_from_toml(config_path: str = "config.toml"):
    """从 TOML 配置初始化日志（兼容旧调用入口）"""
    setup_logging()
