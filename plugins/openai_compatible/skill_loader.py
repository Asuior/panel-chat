# -*- coding: utf-8 -*-
"""技能加载器:从 ``skills/`` 目录扫描并解析技能指令文件。

技能(skill)是面向模型的可复用任务指令集。每个技能是一个 markdown 文件,
顶部用 YAML front-matter 声明元数据,正文是模型需要遵循的执行规范:

- ``name``        技能唯一标识(用于发现与加载)
- ``description`` 技能说明(供模型判断何时使用)
- ``tools``       该技能允许使用的工具名(标识可用工具集合)
- ``flow``        业务流程/子流程步骤(定义业务流程)
- 正文             面向模型的执行规范提示词
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 技能存放目录,默认是本插件目录下的 skills/
SKILLS_DIR = Path(__file__).resolve().parent / "skills"


@dataclass
class Skill:
    """一个已解析的技能。"""

    name: str
    description: str
    tools: list[str] = field(default_factory=list)
    flow: list[str] = field(default_factory=list)
    instructions: str = ""

    def build_prompt(self) -> str:
        """生成注入给模型的完整规范提示词(含业务流程步骤)。"""
        prompt = self.instructions
        if self.flow:
            flow_text = "\n".join(
                f"{i}. {step}" for i, step in enumerate(self.flow, start=1)
            )
            prompt = f"【业务流程】\n{flow_text}\n\n{prompt}"
        return prompt


def _split_frontmatter(text: str) -> tuple[str, str]:
    """把 markdown 拆成 ``(front-matter, 正文)``。"""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[3:end].strip()
            body = text[end + 4 :].lstrip("\n")
            return fm, body
    return "", text


def _parse_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)
    data = yaml.safe_load(fm) or {}

    tools_raw = data.get("tools", "")
    if isinstance(tools_raw, str):
        tools = [t.strip() for t in tools_raw.split(",") if t.strip()]
    elif isinstance(tools_raw, list):
        tools = [t for t in tools_raw if isinstance(t, str)]
    else:
        tools = []

    return Skill(
        name=data.get("name", path.stem),
        description=data.get("description", ""),
        tools=tools,
        flow=[str(s) for s in (data.get("flow") or [])],
        instructions=body.strip(),
    )


def list_skills() -> list[Skill]:
    """扫描 ``skills/`` 目录,返回所有可用技能。"""
    if not SKILLS_DIR.is_dir():
        return []
    skills: list[Skill] = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        try:
            skills.append(_parse_skill(path))
        except Exception:
            # 单个技能文件损坏不阻塞其余技能加载
            continue
    return skills


def load_skill(name: str) -> Skill:
    """按名称加载指定技能;不存在时抛出 ``ValueError``。"""
    for skill in list_skills():
        if skill.name == name:
            return skill
    raise ValueError(f"技能 '{name}' 不存在")
