# -*- coding: utf-8 -*-
"""
对话图片仓库 —— 多模态消息里图片的本地持久化。

设计目标：
    * 本地可回看：图片真正落盘，历史对话重新打开时仍能渲染；
    * 不撑大对话 JSON：消息体里只存相对路径，base64 不写进历史文件；
    * 发送可控：只有“最新一条用户消息”的图片会被组装进请求
      （见 core/chat_payload.py），历史图片一律降级为纯文本。

落盘结构（默认，位于 web/ 下与页面同源，便于 <img src> 直接渲染）：
    web/assets/conversations/<对话id>/<内容哈希>.<ext>

对话 JSON 中记录为：
    {"file": "assets/conversations/<对话id>/<哈希>.png", "name": "截图.png"}
"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from core.logger import get_logger
from core.paths import CONVERSATION_IMAGES_DIR, CONVERSATION_IMAGES_REL

log = get_logger(__name__)

# data URL 头：data:image/png;base64,xxxx（mime 允许缺省）
_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+)?(?:;[^,]*)?;base64,(?P<data>.*)$",
    re.DOTALL,
)
# 允许的图片类型（扩展名 ↔ MIME）
_EXT_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/x-icon": ".ico",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
    "image/tiff": ".tiff",
}
_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".avif": "image/avif",
    ".tiff": "image/tiff",
}
# 单张图片落盘上限（前端限制 8MB，这里留出余量做兜底）
MAX_IMAGE_BYTES = 16 * 1024 * 1024

# 对话 id 仅允许字母数字与 -_（与 ConversationManager 的校验保持一致）
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def is_data_url(value) -> bool:
    """判断是否为 base64 图片 data URL。"""
    return isinstance(value, str) and value.startswith("data:") and ";base64," in value


def decode_data_url(data_url: str) -> tuple[str, bytes]:
    """
    解析 base64 图片 data URL。

    :param data_url: 形如 data:image/png;base64,xxxx
    :return: (mime, 原始字节)
    :raises ValueError: 非 base64 data URL 或内容为空
    """
    if not is_data_url(data_url):
        raise ValueError("不是合法的 base64 data URL")
    match = _DATA_URL_RE.match(data_url)
    if not match:
        raise ValueError("data URL 解析失败")
    mime = (match.group("mime") or "image/png").lower()
    try:
        raw = base64.b64decode(match.group("data"), validate=False)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"图片 base64 解码失败：{exc}") from exc
    if not raw:
        raise ValueError("图片内容为空")
    return mime, raw


def _ext_for(mime: str, name: str = "") -> str:
    """由 MIME 或文件名推断扩展名（未知类型回退 .png）。"""
    if mime in _EXT_BY_MIME:
        return _EXT_BY_MIME[mime]
    suffix = Path(name or "").suffix.lower()
    if suffix in _MIME_BY_EXT:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".png"


def mime_for_ext(ext: str) -> str:
    """扩展名 → MIME（未知回退 image/png）。"""
    return _MIME_BY_EXT.get(str(ext).lower(), "image/png")


class ImageStore:
    """
    对话图片的读写仓库。

    :param base_dir: 图片物理根目录，默认 web/assets/conversations
    :param rel_prefix: 写入对话 JSON 的相对路径前缀（相对 web/）
    """

    def __init__(self, base_dir: Path | None = None, rel_prefix: str = CONVERSATION_IMAGES_REL):
        self.base_dir = Path(base_dir) if base_dir else CONVERSATION_IMAGES_DIR
        self.rel_prefix = rel_prefix.strip("/")

    # ---------- 路径安全 ----------
    def _conv_dir(self, conv_id: str) -> Path:
        safe = "".join(ch for ch in str(conv_id) if ch.isalnum() or ch in "-_")
        if not safe or safe != str(conv_id) or not _SAFE_ID_RE.match(safe):
            raise ValueError(f"非法的对话 id: {conv_id}")
        return self.base_dir / safe

    def rel_for(self, conv_id: str, filename: str) -> str:
        """拼出写入对话 JSON 的相对路径。"""
        return f"{self.rel_prefix}/{conv_id}/{filename}"

    def resolve(self, rel: str) -> Path | None:
        """
        把相对路径还原成物理路径；不在仓库内（或前缀不符）时返回 None。
        """
        if not isinstance(rel, str) or not rel:
            return None
        normalized = rel.replace("\\", "/").strip()
        prefix = self.rel_prefix + "/"
        if not normalized.startswith(prefix):
            return None
        rest = normalized[len(prefix):]
        if not rest or ".." in rest.split("/"):
            return None
        target = (self.base_dir / rest).resolve()
        try:
            target.relative_to(self.base_dir.resolve())
        except ValueError:
            return None
        return target

    # ---------- 写入 ----------
    def save_data_url(self, conv_id: str, data_url: str, name: str = "") -> str | None:
        """
        保存一张图片，返回写入对话 JSON 的相对路径；失败返回 None。

        以内容哈希命名，重复保存（重新生成 / 二次落盘）不会产生副本。
        """
        try:
            mime, raw = decode_data_url(data_url)
        except ValueError as exc:
            log.warning("图片保存跳过：%s", exc)
            return None
        if len(raw) > MAX_IMAGE_BYTES:
            log.warning("图片过大（%.1fMB），未落盘", len(raw) / 1024 / 1024)
            return None
        digest = hashlib.sha1(raw).hexdigest()[:16]
        filename = f"{digest}{_ext_for(mime, name)}"
        try:
            folder = self._conv_dir(conv_id)
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / filename
            if not target.exists():
                target.write_bytes(raw)
            return self.rel_for(conv_id, filename)
        except (OSError, ValueError) as exc:
            log.error("图片落盘失败(conv=%s)：%s", conv_id, exc)
            return None

    # ---------- 读取 ----------
    def to_data_url(self, rel: str) -> str | None:
        """把仓库内的相对路径读成 data URL（供发送给 AI 时使用）。"""
        path = self.resolve(rel)
        if path is None or not path.is_file():
            return None
        try:
            raw = path.read_bytes()
        except OSError as exc:
            log.error("读取图片失败 %s：%s", rel, exc)
            return None
        mime = mime_for_ext(path.suffix)
        return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")

    def entry_to_data_url(self, entry) -> str | None:
        """
        把消息里的图片条目（dict 或字符串）转成 data URL。

        兼容三种形态：
            {"data": "data:image/png;base64,..."}  前端刚选好、尚未落盘
            {"file": "assets/conversations/..."}   已落盘
            "data:..." / "assets/conversations/..." 裸字符串
        """
        if isinstance(entry, str):
            return entry if is_data_url(entry) else self.to_data_url(entry)
        if not isinstance(entry, dict):
            return None
        for key in ("data", "data_url", "url", "file", "src"):
            value = entry.get(key)
            if is_data_url(value):
                return value
        for key in ("file", "url", "src"):
            value = entry.get(key)
            if isinstance(value, str) and value:
                found = self.to_data_url(value)
                if found:
                    return found
        return None

    # ---------- 清理 ----------
    def prune(self, conv_id: str, keep_rels) -> int:
        """
        删除该对话目录中不再被任何消息引用的图片，返回删除数量。

        :param keep_rels: 仍被引用的相对路径集合
        """
        folder = self._conv_dir(conv_id)
        if not folder.is_dir():
            return 0
        keep_names = set()
        for rel in keep_rels:
            path = self.resolve(rel)
            if path is not None:
                keep_names.add(path.name)
        removed = 0
        try:
            for item in folder.iterdir():
                if not item.is_file() or item.name in keep_names:
                    continue
                item.unlink()
                removed += 1
        except OSError as exc:
            log.warning("清理对话图片失败(conv=%s)：%s", conv_id, exc)
        return removed

    def delete_conversation(self, conv_id: str) -> int:
        """删除整个对话的图片目录，返回删除的文件数。"""
        folder = self._conv_dir(conv_id)
        if not folder.is_dir():
            return 0
        removed = 0
        try:
            for item in folder.iterdir():
                if item.is_file():
                    item.unlink()
                    removed += 1
            folder.rmdir()
        except OSError as exc:
            log.warning("删除对话图片目录失败(conv=%s)：%s", conv_id, exc)
        return removed


# 默认仓库（全局单例，供 Api / ConversationManager 复用）
default_store = ImageStore()
