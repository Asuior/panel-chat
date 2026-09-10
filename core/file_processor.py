# -*- coding: utf-8 -*-
"""
文件读取 / 写入处理（悬浮球拖拽文件 → AI 处理 → 写回）。

流程：
    1. 校验扩展名与大小；
    2. 以 UTF-8（回退 GB18030）读取文本内容；
    3. 组合系统提示词（已启用的提示词分组）+ 指令模板 + 文件内容；
    4. 调用当前 AI 接口 chat()；
    5. 按配置 覆盖(overwrite) / 追加(append) 写回原文件。

本模块不依赖 GUI，chat 由调用方注入，便于无头测试。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from core.logger import get_logger

log = get_logger(__name__)

# 可处理的文本类扩展名
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".py", ".js", ".jsx", ".ts", ".tsx", ".json",
    ".csv", ".tsv", ".log", ".ini", ".cfg", ".conf", ".toml", ".yaml", ".yml",
    ".xml", ".html", ".htm", ".css", ".scss", ".less", ".sql", ".sh", ".bat",
    ".ps1", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb",
    ".php", ".swift", ".kt", ".vue", ".svelte", ".rst", ".tex", ".properties",
    ".env", ".gitignore", ".dockerfile", ".lock", ".plist",
}

# 无扩展名但通常可读的文件
NAMED_TEXT_FILES = {"dockerfile", "makefile", "license", "readme", "changelog"}

_MAX_READ = 500_000  # 硬上限 500KB


def is_text_file(path: Path) -> bool:
    """判断扩展名是否属于可处理的文本类文件。"""
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return True
    return path.name.lower() in NAMED_TEXT_FILES


def read_text_file(path: Path, max_chars: int = _MAX_READ) -> str:
    """读取文本文件（UTF-8 优先，回退 GB18030）。超限抛 ValueError。"""
    size = path.stat().st_size
    if size > max_chars * 2:  # 粗略按字节上限 2 倍保护
        raise ValueError(f"文件过大（{size} 字节），已拒绝处理。上限约 {max_chars} 字符。")

    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("无法识别的文件编码（尝试了 UTF-8 / GB18030）。")

    if len(text) > max_chars:
        raise ValueError(
            f"文件内容过长（{len(text)} 字符），超出处理上限 {max_chars} 字符。"
        )
    return text


def write_text_file(path: Path, content: str, mode: str = "overwrite") -> None:
    """
    写回文件。

    :param mode: overwrite（覆盖原文） | append（追加到原文末尾）
    """
    path = Path(path)
    if mode == "append":
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n\n" + content)
    else:
        path.write_text(content, encoding="utf-8")


class FileProcessor:
    """
    文件处理编排器。

    :param chat_callable: callable(messages: list, temperature: float) -> str
    :param compose_system: callable() -> str 返回当前组合好的系统提示词
    :param config_provider: callable() -> dict 返回文件处理相关配置
    """

    def __init__(
        self,
        chat_callable: Callable,
        compose_system: Callable[[], str],
        config_provider: Callable[[], dict],
    ):
        self._chat = chat_callable
        self._compose_system = compose_system
        self._config = config_provider

    # ------------------------------------------------------------------ #
    def process(self, file_path: str, temperature: float = 0.7) -> dict:
        """
        处理单个文件。返回结果字典，供 API 层回传前端：

        {"ok": bool, "path": str, "name": str,
         "mode": "overwrite|append|none", "message": str, "error": str|None}
        """
        result = {
            "ok": False,
            "path": file_path,
            "name": os.path.basename(file_path),
            "mode": None,
            "message": "",
            "error": None,
        }
        cfg = self._config() or {}
        path = Path(file_path)

        if not path.exists():
            result["error"] = f"文件不存在：{file_path}"
            return result
        if not is_text_file(path):
            result["error"] = (
                f"不支持的文件类型「{path.suffix or path.name}」。"
                f"支持：{' '.join(sorted(TEXT_EXTENSIONS)[:20])} 等文本文件。"
            )
            return result

        try:
            content = read_text_file(path, cfg.get("max_chars", _MAX_READ))
        except (OSError, ValueError) as exc:
            result["error"] = f"读取文件失败：{exc}"
            return result

        # 组合消息：系统提示词(已选分组) + 指令模板 + 文件内容
        system_prompt = self._compose_system()
        instruction = (cfg.get("instruction") or "").strip()
        header = system_prompt or "你是一个可靠的文件处理助手。"
        if instruction:
            header = f"{header}\n\n## 处理要求\n{instruction}"
        messages = [
            {"role": "system", "content": header},
            {
                "role": "user",
                "content": f"## 文件名\n{result['name']}\n\n## 文件内容\n```\n{content}\n```",
            },
        ]

        try:
            reply = self._chat(messages, temperature).strip()
        except Exception as exc:  # noqa: BLE001 —— 统一包装为友好错误
            log.exception("文件处理时 AI 调用失败")
            result["error"] = f"AI 处理失败：{exc}"
            return result

        if not reply:
            result["error"] = "AI 返回了空内容，未写回文件。"
            return result

        # 写回
        mode = cfg.get("mode", "overwrite")
        try:
            write_text_file(path, reply, mode)
        except OSError as exc:
            result["error"] = f"写回文件失败：{exc}"
            return result

        result["ok"] = True
        result["mode"] = mode
        result["message"] = (
            f"已处理「{result['name']}」（{mode}模式），AI 输出 {len(reply)} 字符。"
        )
        log.info("文件处理成功: %s mode=%s", file_path, mode)
        return result
