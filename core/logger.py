# -*- coding: utf-8 -*-
"""
日志模块：同时输出到控制台与 logs/app.log（UTF-8、按大小轮转）。

用法：
    from core.logger import get_logger
    log = get_logger(__name__)
    log.info(...)
"""
from __future__ import annotations

import logging
import logging.handlers
import sys

from core.paths import LOG_DIR

_configured = False
_LEVEL = logging.INFO


def setup_logging(level: int = logging.INFO) -> None:
    """初始化全局日志。多次调用是幂等的。"""
    global _configured, _LEVEL
    _LEVEL = level
    if _configured:
        return
    _configured = True

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # 控制台 handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    # 文件 handler（RotatingFileHandler，UTF-8）
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_DIR / "app.log",
            maxBytes=2 * 1024 * 1024,  # 2 MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:  # 日志目录不可写时仅保留控制台输出
        pass


def get_logger(name: str) -> logging.Logger:
    """获取带模块名的 logger；若尚未 setup 则先 setup。"""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
