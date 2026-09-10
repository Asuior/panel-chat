# -*- coding: utf-8 -*-
"""
路径常量与目录初始化。

统一存放项目根目录下的关键路径：
    data/       本地数据（对话、索引、设置、提示词、主题副本）
    logs/       运行日志
    plugins/    AI 接口插件目录
    web/        前端静态资源
    themes/     主题目录（每个子文件夹一个主题）

注意：本模块不依赖任何第三方库，可在无头环境下使用。
"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根目录：本文件位于 <root>/core/paths.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 核心数据目录
DATA_DIR = PROJECT_ROOT / "data"
CONVERSATIONS_DIR = DATA_DIR / "conversations"
THEMES_DATA_DIR = DATA_DIR / "themes"          # 用户自定义主题副本（备用）
ASSETS_BG_DIR = DATA_DIR / "backgrounds"       # 用户上传的背景图片

# 数据文件
SETTINGS_FILE = DATA_DIR / "settings.json"
PROMPTS_FILE = DATA_DIR / "prompts.json"
HISTORY_INDEX_FILE = DATA_DIR / "history_index.json"

# 其它目录
LOG_DIR = PROJECT_ROOT / "logs"
PLUGIN_DIR = PROJECT_ROOT / "plugins"
WEB_DIR = PROJECT_ROOT / "web"
THEMES_DIR = PROJECT_ROOT / "themes"

# 内置（随程序分发）的默认主题文件夹
BUILTIN_THEME_DIR = THEMES_DIR / "glassmorphism"


def ensure_dirs() -> None:
    """确保所有需要落盘的目录存在（幂等）。"""
    for folder in (
        DATA_DIR,
        CONVERSATIONS_DIR,
        LOG_DIR,
        PLUGIN_DIR,
        WEB_DIR,
        THEMES_DIR,
        THEMES_DATA_DIR,
        ASSETS_BG_DIR,
        WEB_DIR / "assets",
        WEB_DIR / "assets" / "backgrounds",
        WEB_DIR / "assets" / "icons",
    ):
        folder.mkdir(parents=True, exist_ok=True)


def web_url(path: Path | str) -> str:
    """把 web/ 下的相对路径转换成 pywebview 可用的 file:// 绝对 URL。"""
    p = Path(path)
    if not p.is_absolute():
        p = WEB_DIR / p
    return p.resolve().as_uri()


def theme_dir(name: str) -> Path:
    """返回某个主题的目录（不存在则返回路径本身）。"""
    return THEMES_DIR / name


def log_path() -> Path:
    return LOG_DIR / "app.log"
