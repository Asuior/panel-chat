# -*- coding: utf-8 -*-
"""
系统提示词管理（prompts.json）。

数据结构 v2（每个分组含多条提示词）：
{
  "groups": [
    {
      "id": "g_xxx",
      "name": "编程助手",
      "created_at": "ISO",
      "prompts": [
        {"id": "p_xxx", "content": "你是资深程序员。", "created_at": "ISO"},
        {"id": "p_yyy", "content": "优先给出可运行的代码。"}
      ]
    }
  ]
}

兼容性：读取到 v1（分组直接带 "content" 字符串）时自动迁移为上面的结构。

组合逻辑：按启用顺序取分组，分组内的提示词按顺序逐一拼接，
分组之间也以空行分隔，最终作为系统提示词使用。
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone

from core.logger import get_logger
from core.paths import PROMPTS_FILE

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _normalize_group(group: dict) -> dict:
    """把旧/脏结构统一为 v2：{id,name,created_at,prompts:[{id,content,...}]}。"""
    gid = group.get("id") or _new_id("g")
    created = group.get("created_at") or _now_iso()
    raw_prompts = group.get("prompts")
    if raw_prompts is None and "content" in group:
        # v1 迁移：单条 content 转为一个 prompt
        raw_prompts = [{"content": group.get("content", "")}]
    prompts = []
    for p in raw_prompts or []:
        if not isinstance(p, dict):
            continue
        prompts.append({
            "id": p.get("id") or _new_id("p"),
            "content": str(p.get("content", "")),
            "created_at": p.get("created_at") or _now_iso(),
        })
    return {
        "id": gid,
        "name": str(group.get("name") or "未命名分组"),
        "created_at": created,
        "prompts": prompts,
    }


class PromptManager:
    """提示词分组的读写封装（线程安全）。"""

    def __init__(self, file_path=None):
        self._file = file_path or PROMPTS_FILE
        self._lock = threading.RLock()
        self._groups: list[dict] = []
        self.load()

    # ---------- 数据访问 ----------
    def load(self) -> list[dict]:
        with self._lock:
            if self._file.exists():
                try:
                    raw = json.loads(self._file.read_text(encoding="utf-8"))
                    groups = raw.get("groups", []) if isinstance(raw, dict) else []
                except (OSError, ValueError) as exc:
                    log.warning("prompts.json 解析失败，使用空提示词库: %s", exc)
                    groups = []
            else:
                groups = []
            self._groups = [_normalize_group(g) for g in groups]
            return self.get_groups()

    def get_groups(self) -> list[dict]:
        """返回全部分组（深拷贝，含各自 prompts 列表）。"""
        with self._lock:
            return json.loads(json.dumps(self._groups))

    def count_enabled_prompts(self, group_ids: list[str]) -> int:
        """统计给定分组序中实际会拼接的提示词条数（供前端展示）。"""
        with self._lock:
            by_id = {g["id"]: g for g in self._groups}
            total = 0
            for gid in group_ids:
                g = by_id.get(gid)
                if g:
                    total += len([p for p in g.get("prompts", [])
                                  if str(p.get("content", "")).strip()])
            return total

    # ---------- 分组增删改 ----------
    def add_group(self, name: str, content: str | None = None) -> dict:
        """新增分组。content 非空时同时放入第一条提示词。"""
        with self._lock:
            prompts = []
            if content is not None and str(content).strip():
                prompts.append({
                    "id": _new_id("p"),
                    "content": str(content),
                    "created_at": _now_iso(),
                })
            group = {
                "id": _new_id("g"),
                "name": (name or "未命名分组").strip(),
                "created_at": _now_iso(),
                "prompts": prompts,
            }
            self._groups.append(group)
            self.save()
            return json.loads(json.dumps(group))

    def update_group(self, group_id: str, name: str | None) -> bool:
        with self._lock:
            for g in self._groups:
                if g["id"] == group_id:
                    if name is not None:
                        g["name"] = (name or "未命名分组").strip()
                    self.save()
                    return True
            return False

    def delete_group(self, group_id: str) -> bool:
        with self._lock:
            before = len(self._groups)
            self._groups = [g for g in self._groups if g["id"] != group_id]
            changed = len(self._groups) != before
            if changed:
                self.save()
            return changed

    # ---------- 分组内提示词增删改 ----------
    def add_prompt(self, group_id: str, content: str = "") -> dict | None:
        with self._lock:
            for g in self._groups:
                if g["id"] == group_id:
                    prompt = {
                        "id": _new_id("p"),
                        "content": str(content),
                        "created_at": _now_iso(),
                    }
                    g.setdefault("prompts", []).append(prompt)
                    self.save()
                    return json.loads(json.dumps(prompt))
            return None

    def update_prompt(self, group_id: str, prompt_id: str, content: str) -> bool:
        with self._lock:
            for g in self._groups:
                if g["id"] == group_id:
                    for p in g.get("prompts", []):
                        if p["id"] == prompt_id:
                            p["content"] = str(content)
                            self.save()
                            return True
            return False

    def delete_prompt(self, group_id: str, prompt_id: str) -> bool:
        with self._lock:
            for g in self._groups:
                if g["id"] == group_id:
                    before = len(g.get("prompts", []))
                    g["prompts"] = [p for p in g.get("prompts", [])
                                    if p["id"] != prompt_id]
                    changed = len(g["prompts"]) != before
                    if changed:
                        self.save()
                    return changed
            return False

    def replace_all(self, groups: list[dict]) -> None:
        """整体替换（前端批量保存用）。"""
        with self._lock:
            self._groups = [_normalize_group(g) for g in groups]
            self.save()

    # ---------- 组合提示词 ----------
    def compose(self, group_ids: list[str], joiner: str = "\n\n") -> str:
        """
        按给定顺序拼接若干分组中的所有提示词内容。

        规则：分组按 group_ids 顺序；分组内提示词按数组顺序；
        提示词之间、分组之间都用空行分隔。
        """
        with self._lock:
            by_id = {g["id"]: g for g in self._groups}
            parts = []
            for gid in group_ids:
                g = by_id.get(gid)
                if not g:
                    continue
                group_parts = [
                    str(p.get("content", "")).strip()
                    for p in g.get("prompts", [])
                    if str(p.get("content", "")).strip()
                ]
                if group_parts:
                    parts.append(joiner.join(group_parts))
            return joiner.join(parts).strip()

    def save(self) -> None:
        with self._lock:
            try:
                self._file.parent.mkdir(parents=True, exist_ok=True)
                self._file.write_text(
                    json.dumps({"groups": self._groups}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                log.error("写入 prompts.json 失败: %s", exc)
