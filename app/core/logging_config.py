"""
日志配置模块
整合自 knowledge_graph/logger.py
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from app.core.config import get_settings


def setup_logging():
    """配置日志系统"""
    settings = get_settings()

    logger = logging.getLogger()
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    # 清除已有的处理器
    logger.handlers.clear()

    # 格式化器
    formatter = logging.Formatter(
        settings.LOG_FORMAT,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件输出（10MB 轮转，保留 5 个备份）
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
    """获取日志记录器"""
    return logging.getLogger(name)


# 兼容旧代码的函数
def config_logger_from_toml(config_path: str = "config.toml"):
    """从 TOML 配置文件初始化日志（兼容旧代码）"""
    setup_logging()
