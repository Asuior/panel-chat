# -*- coding: utf-8 -*-
"""
对话管理（conversations/*.json + history_index.json）。

每个对话一个文件：
    data/conversations/{uuid}.json
    {
      "id": "uuid",
      "title": "对话标题（自动/手动）",
      "created_at": "ISO",
      "updated_at": "ISO",
      "messages": [{"role": "user|assistant", "content": "...", "timestamp": "ISO"}]
    }

索引文件 history_index.json：
    {"<id>": {"title": ..., "preview": ..., "created_at": ..., "updated_at": ...}}

历史列表（get_conversations）即索引按 updated_at 倒序，
配合 preview（最新一条消息摘要），重启后可快速恢复会话列表。
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from core.logger import get_logger
from core.paths import CONVERSATIONS_DIR, HISTORY_INDEX_FILE

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _shorten(text: str, limit: int = 40) -> str:
    """截断用于标题/预览的文本。"""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ConversationManager:
    """对话 CRUD + 索引维护（线程安全）。"""

    def __init__(self, conv_dir=None, index_file=None):
        self._dir = conv_dir or CONVERSATIONS_DIR
        self._index_file = index_file or HISTORY_INDEX_FILE
        self._lock = threading.RLock()
        self._index: dict[str, dict] = {}
        self._dir.mkdir(parents=True, exist_ok=True)
        self._load_index()

    # ---------- 索引 ----------
    def _load_index(self) -> None:
        if self._index_file.exists():
            try:
                self._index = json.loads(self._index_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("history_index.json 解析失败: %s", exc)
                self._index = {}

    def _save_index(self) -> None:
        try:
            self._index_file.write_text(
                json.dumps(self._index, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            log.error("写入 history_index.json 失败: %s", exc)

    def list_conversations(self) -> list[dict]:
        """
        返回历史列表：[{id, title, preview, created_at, updated_at}]，
        按 updated_at 倒序（最新在前）。
        """
        with self._lock:
            items = []
            for cid, meta in self._index.items():
                items.append(
                    {
                        "id": cid,
                        "title": meta.get("title", "新对话"),
                        "preview": meta.get("preview", ""),
                        "created_at": meta.get("created_at", ""),
                        "updated_at": meta.get("updated_at", ""),
                    }
                )
            items.sort(key=lambda it: it.get("updated_at", ""), reverse=True)
            return items

    # ---------- 读写对话 ----------
    def _file(self, conv_id: str):
        # 校验 conv_id 仅允许 uuid 形态字符，防止路径穿越
        safe = "".join(ch for ch in str(conv_id) if ch.isalnum() or ch in "-_")
        if safe != str(conv_id):
            raise ValueError(f"非法的对话 id: {conv_id}")
        return self._dir / f"{conv_id}.json"

    def get_conversation(self, conv_id: str) -> dict | None:
        """返回完整对话（messages 在内），不存在返回 None。"""
        with self._lock:
            path = self._file(conv_id)
            if not path.exists():
                return None
            try:
                conv = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.error("读取对话 %s 失败: %s", conv_id, exc)
                return None
            # 若索引缺失（文件被手工放入），补建索引
            if conv_id not in self._index:
                self._rebuild_index_entry(conv)
            return conv

    def _derive_meta(
        self,
        messages: list[dict],
        old_meta: dict | None,
        explicit_title: str | None = None,
    ) -> dict:
        """
        由消息列表推导 title / preview / timestamps。

        标题优先级：
            1) 本次显式命名 explicit_title（手动重命名/新建指定名）
            2) 历史手动命名（title_manual=True）
            3) 自动以首条用户消息命名（仅一次，之后保留）
        若始终没有可用的标题，则使用“新对话”。
        """
        old_meta = old_meta or {}
        first_user = next((m for m in messages if m.get("role") == "user"), None)
        last_msg = messages[-1] if messages else None

        manual = bool(old_meta.get("title_manual", False))
        title = old_meta.get("title", "") or "新对话"
        if explicit_title is not None and explicit_title.strip():
            title = explicit_title.strip()
            manual = True
        elif manual:
            title = old_meta.get("title") or "新对话"
        elif not old_meta.get("title") and first_user:
            # 新对话首次落盘：用首条用户消息自动命名
            title = _shorten(str(first_user.get("content", "")), 20) or "新对话"
        preview = _shorten(str(last_msg.get("content", ""))) if last_msg else ""

        timestamps = [m.get("timestamp", "") for m in messages if m.get("timestamp")]
        updated = max(timestamps) if timestamps else _now_iso()
        return {
            "title": title,
            "title_manual": manual,
            "preview": preview,
            "created_at": old_meta.get("created_at", _now_iso()),
            "updated_at": updated,
        }

    def save_conversation(self, conv_id: str, messages: list[dict], title: str | None = None) -> dict:
        """
        保存对话全部消息（整段替换），自动维护索引与标题。
        messages: [{"role","content","timestamp"}...]
        返回 {id, title, created_at, updated_at, messages}
        """
        with self._lock:
            now = _now_iso()
            old_conv = self.get_conversation(conv_id)  # 小心：内部有锁（RLock 可重入）
            old_meta = self._index.get(conv_id) or (
                {"title": old_conv.get("title") if old_conv else None,
                 "created_at": old_conv.get("created_at") if old_conv else None}
                if old_conv else None
            )

            # 规范化消息
            clean_msgs = []
            for m in messages or []:
                if not isinstance(m, dict):
                    continue
                clean_msgs.append(
                    {
                        "role": "user" if m.get("role") == "user" else "assistant",
                        "content": str(m.get("content", "")),
                        "timestamp": m.get("timestamp") or now,
                    }
                )

            # 规范化消息
            clean_msgs = []
            for m in messages or []:
                if not isinstance(m, dict):
                    continue
                clean_msgs.append(
                    {
                        "role": "user" if m.get("role") == "user" else "assistant",
                        "content": str(m.get("content", "")),
                        "timestamp": m.get("timestamp") or now,
                    }
                )

            meta = self._derive_meta(clean_msgs, old_meta or {}, explicit_title=title)
            if old_meta and old_meta.get("title_manual") and title is None:
                meta["title"] = old_meta["title"]

            conv = {
                "id": str(conv_id),
                "title": meta["title"],
                "created_at": meta["created_at"],
                "updated_at": meta["updated_at"],
                "messages": clean_msgs,
            }
            path = self._file(conv_id)
            path.write_text(
                json.dumps(conv, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._index[conv_id] = {
                "title": conv["title"],
                "title_manual": meta["title_manual"],
                "preview": meta["preview"],
                "created_at": conv["created_at"],
                "updated_at": conv["updated_at"],
            }
            self._save_index()
            return json.loads(json.dumps(conv))

    def create_conversation(self, conv_id: str | None = None, title: str = "新对话") -> dict:
        """新建空对话。前端通常在发出第一条消息时才真正落盘。"""
        cid = conv_id or ("conv_" + uuid.uuid4().hex)
        if cid not in self._index:
            return self.save_conversation(cid, [], title=title)
        return self.get_conversation(cid)

    # ---------- 删除 / 截断 / 修改 ----------
    def delete_conversation(self, conv_id: str) -> bool:
        """删除整个对话（文件 + 索引）。"""
        with self._lock:
            path = self._file(conv_id)
            existed = path.exists()
            if existed:
                try:
                    path.unlink()
                except OSError as exc:
                    log.error("删除对话文件失败 %s: %s", conv_id, exc)
                    return False
            if conv_id in self._index:
                del self._index[conv_id]
                self._save_index()
            return existed or conv_id in self._index

    def delete_from(self, conv_id: str, msg_index: int) -> dict | None:
        """
        删除第 msg_index 条消息及其之后的所有消息（分支截断）。
        返回截断后的对话；若没有剩余消息则删除该对话并返回 None。
        """
        with self._lock:
            conv = self.get_conversation(conv_id)
            if not conv:
                return None
            messages = conv.get("messages", [])
            index = max(0, min(int(msg_index), len(messages)))
            kept = messages[:index]
            if not kept:
                self.delete_conversation(conv_id)
                return None
            return self.save_conversation(conv_id, kept)

    def update_message(self, conv_id: str, msg_index: int, new_content: str) -> dict | None:
        """修改第 msg_index 条消息内容（本地编辑，不触发 AI 重生成）。"""
        with self._lock:
            conv = self.get_conversation(conv_id)
            if not conv:
                return None
            messages = conv.get("messages", [])
            index = int(msg_index)
            if not (0 <= index < len(messages)):
                return None
            messages[index] = dict(messages[index])
            messages[index]["content"] = str(new_content)
            messages[index]["timestamp"] = _now_iso()
            return self.save_conversation(conv_id, messages)

    def rename_conversation(self, conv_id: str, title: str) -> bool:
        """手动重命名对话。"""
        with self._lock:
            conv = self.get_conversation(conv_id)
            if not conv:
                return False
            conv["title"] = (title or "").strip() or conv["title"]
            self.save_conversation(conv_id, conv.get("messages", []), title=conv["title"])
            return True

    def _rebuild_index_entry(self, conv: dict) -> None:
        cid = conv.get("id", "")
        if not cid:
            return
        messages = conv.get("messages", [])
        meta = self._derive_meta(messages, {})
        self._index[cid] = {
            "title": conv.get("title") or meta["title"],
            "preview": meta["preview"],
            "created_at": conv.get("created_at") or meta["created_at"],
            "updated_at": conv.get("updated_at") or meta["updated_at"],
        }
        self._save_index()
