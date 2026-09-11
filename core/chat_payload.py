# -*- coding: utf-8 -*-
"""
发送给 AI 接口前的消息过滤与组装。

背景：
    本地对话会完整保留图片（便于回看），但把所有历史图片都塞进请求
    既浪费上下文，多数接口也不接受。因此发送前统一做一次过滤：

        * 只有“最后一条用户消息”的图片会被组装成多模态 parts；
        * 更早的消息一律降级为纯文本（图片被丢弃）；
        * 接口未开启多模态时，所有图片都被丢弃；
        * 输出只保留 role / content 两个字段，避免把本地字段
          （timestamp / images 等）透传给接口导致报错。

被丢弃的图片不影响本地渲染，本地图片由前端直接读取消息里的
images 字段展示（见 web/js/chat.js）。
"""
from __future__ import annotations

from typing import Any, Callable

# 图片解析器签名：接收消息里的图片条目，返回 base64 data URL（不可用时返回 None）
ImageResolver = Callable[[Any], "str | None"]


def normalize_content(content: Any) -> tuple[str, list[str]]:
    """
    把一条消息的 content 归一化为 (纯文本, 图片 data URL 列表)。

    支持两种形态：
        "文本"
        [{"type": "text", "text": ...}, {"type": "image_url", "image_url": {"url": ...}}]
    """
    if isinstance(content, list):
        texts: list[str] = []
        images: list[str] = []
        for part in content:
            if isinstance(part, str):
                if part:
                    texts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                texts.append(part["text"])
                continue
            image = part.get("image_url") or part.get("image") or part.get("source")
            if isinstance(image, dict):
                image = image.get("url") or image.get("data") or image.get("base64")
            if isinstance(image, str) and image:
                images.append(image)
        return "\n".join(t for t in texts if t), images
    if content is None:
        return "", []
    return str(content), []


def collect_images(message: dict, resolver: ImageResolver | None) -> list[str]:
    """
    收集一条消息携带的图片（统一成 data URL）。

    来源有两处：
        * content 里已经是 parts 形态的 image_url；
        * 消息自带的 images 字段（本地对话记录格式）。
    """
    images: list[str] = []
    for url in normalize_content(message.get("content"))[1]:
        if url and url not in images:
            images.append(url)
    raw = message.get("images") or []
    if isinstance(raw, dict):
        raw = [raw]
    for entry in raw:
        url = None
        if isinstance(entry, str) and entry.startswith("data:"):
            url = entry
        elif resolver is not None:
            try:
                url = resolver(entry)
            except Exception:  # noqa: BLE001 —— 单张图片失败不应中断整条请求
                url = None
        if url and url not in images:
            images.append(url)
    return images


def last_user_index(messages: list) -> int:
    """返回最后一条 user 消息的下标；没有则返回 -1。"""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], dict) and messages[i].get("role") == "user":
            return i
    return -1


def build_chat_payload(
    messages: list,
    allow_images: bool = False,
    resolver: ImageResolver | None = None,
) -> list[dict]:
    """
    组装最终发给 AI 接口的消息列表。

    :param messages: 本地消息列表（可含 timestamp / images 等本地字段）
    :param allow_images: 当前接口是否允许图片输入
    :param resolver: 图片条目 → data URL 的解析函数（通常来自 ImageStore）
    :return: [{"role": ..., "content": str | list}]，可直接投喂插件
    """
    target = last_user_index(messages) if allow_images else -1
    payload: list[dict] = []
    for index, message in enumerate(messages or []):
        if not isinstance(message, dict):
            continue
        role = "user" if message.get("role") == "user" else (
            "system" if message.get("role") == "system" else "assistant"
        )
        text = normalize_content(message.get("content"))[0]
        images = collect_images(message, resolver) if index == target else []
        if images:
            parts: list[dict] = []
            if text:
                parts.append({"type": "text", "text": text})
            parts.extend({"type": "image_url", "image_url": {"url": url}} for url in images)
            payload.append({"role": role, "content": parts})
        else:
            payload.append({"role": role, "content": text})
    return payload


def summary(payload: list) -> dict:
    """统计过滤结果（供日志/测试使用）。"""
    text_count = 0
    image_count = 0
    for message in payload or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            image_count += sum(1 for p in content if isinstance(p, dict) and p.get("type") == "image_url")
        else:
            text_count += 1
    return {"messages": len(payload or []), "text_only": text_count, "images": image_count}
