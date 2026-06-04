"""
WealthPilot Skills Loader (v3.0)

按 Anthropic Agent Skills 开放标准加载本地 SKILL.md 文件夹。

v2.6 能力：discover / load_skill_body / get_skill / get_skill_metadatas / list_skill_names
v3.0 新增：invoke（根据 type 字段分发到 function_call / llm_dispatch / prompt_inject / validation）
"""
from __future__ import annotations

import importlib
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# WealthPilot 项目根目录下的 skills/ 目录
SKILLS_ROOT = Path(__file__).parent.parent.parent / "skills"


@dataclass
class SkillMeta:
    """Skill 元数据，对应 SKILL.md 的 YAML frontmatter。"""
    name: str
    description: str
    version: str = "1.0.0"
    tags: list[str] = field(default_factory=list)
    intent_binding: Optional[str] = None
    file_path: str = ""
    body: str = ""

    # v3.0 扩展字段
    type: str = "function_call"   # function_call / llm_dispatch / prompt_inject / validation
    entry_point: Optional[str] = None  # 函数引用，如 "module.path:func_name"
    tool_name: Optional[str] = None    # function_call 类型对应的 M2 Tool 名
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)

    # llm_dispatch 类型专用
    prompt_templates_dir: Optional[str] = None

    # prompt_inject 类型专用
    injection_target: Optional[str] = None

    # function_call 类型可能引用其他 Tool
    related_tools: list[str] = field(default_factory=list)


class SkillsLoader:
    """Skills 加载器，单例。"""

    def __init__(self, skills_root: Path = SKILLS_ROOT):
        self.skills_root = skills_root
        self._skills: dict[str, SkillMeta] = {}
        self._loaded = False
        self._lock = threading.Lock()

    def discover(self) -> None:
        """扫描 skills_root 目录，发现并加载所有 SKILL.md。"""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._skills.clear()
            if not self.skills_root.exists():
                self._loaded = True
                return

            for skill_dir in self.skills_root.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue
                try:
                    meta = self._parse_skill_md(skill_md)
                    if meta:
                        self._skills[meta.name] = meta
                except Exception as e:
                    print(f"[SkillsLoader] 解析 {skill_md} 失败: {e}")
            self._loaded = True

    def _parse_skill_md(self, path: Path) -> Optional[SkillMeta]:
        """解析单个 SKILL.md 文件，提取 frontmatter 和 body。"""
        content = path.read_text(encoding="utf-8")
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
        if not match:
            return None

        frontmatter_text = match.group(1)
        body = match.group(2).strip()

        try:
            fm = yaml.safe_load(frontmatter_text) or {}
        except yaml.YAMLError:
            return None

        return SkillMeta(
            name=fm.get("name", path.parent.name),
            description=fm.get("description", ""),
            version=fm.get("version", "1.0.0"),
            tags=fm.get("tags", []) or [],
            intent_binding=fm.get("intent_binding"),
            file_path=str(path),
            body=body,
            # v3.0 扩展字段
            type=fm.get("type", "function_call"),
            entry_point=fm.get("entry_point"),
            tool_name=fm.get("tool_name"),
            inputs=fm.get("inputs", {}) or {},
            outputs=fm.get("outputs", {}) or {},
            prompt_templates_dir=fm.get("prompt_templates_dir"),
            injection_target=fm.get("injection_target"),
            related_tools=fm.get("related_tools", []) or [],
        )

    def get_skill_metadatas(self) -> list[SkillMeta]:
        """返回所有已发现的 Skill 元数据。"""
        self.discover()
        return list(self._skills.values())

    def load_skill_body(self, skill_name: str) -> Optional[str]:
        """按名称加载 Skill 的完整 body。"""
        self.discover()
        meta = self._skills.get(skill_name)
        return meta.body if meta else None

    def get_skill(self, skill_name: str) -> Optional[SkillMeta]:
        """按名称获取完整 SkillMeta。"""
        self.discover()
        return self._skills.get(skill_name)

    def list_skill_names(self) -> list[str]:
        """列出所有 Skill 名称。"""
        self.discover()
        return list(self._skills.keys())

    # ════════════════════════════════════════════════
    # v3.0 Step 7c：Skill invoke 统一入口
    # ════════════════════════════════════════════════

    def invoke(self, skill_name: str, **params) -> object:
        """
        调用 Skill。根据 SKILL.md 的 type 字段分发到不同 invoke 方法。

        4 种 type：
        - function_call：通过 M2 Tool Layer 调用
        - llm_dispatch：v3.0 阶段不支持直接 invoke
        - prompt_inject：返回 SKILL.md 的 body 文本
        - validation：调用 entry_point 指向的校验函数
        """
        self.discover()
        meta = self._skills.get(skill_name)
        if not meta:
            raise ValueError(f"Skill 不存在: {skill_name}")

        if meta.type == "function_call":
            return self._invoke_function_call(meta, **params)
        elif meta.type == "llm_dispatch":
            return self._invoke_llm_dispatch(meta, **params)
        elif meta.type == "prompt_inject":
            return self._invoke_prompt_inject(meta, **params)
        elif meta.type == "validation":
            return self._invoke_validation(meta, **params)
        else:
            raise ValueError(f"未知 Skill 类型: {meta.type} (skill={skill_name})")

    def _invoke_function_call(self, meta: SkillMeta, **params) -> object:
        """function_call 类型：通过 M2 Tool Layer 的 call_tool 调用。"""
        if not meta.tool_name:
            raise ValueError(
                f"function_call Skill 缺少 tool_name 字段: {meta.name}"
            )
        from backend.graph.tools import call_tool
        return call_tool(meta.tool_name, **params)

    # C0: prompt_template_id → 模块内函数名 的白名单映射
    # C0 仅放行 general_chat；reason 类待 C6 补齐
    _LLM_DISPATCH_SUPPORTED: dict[str, str] = {
        "general_chat": "chat",
    }

    def _invoke_llm_dispatch(self, meta: SkillMeta, **params) -> object:
        """
        llm_dispatch 类型：按 prompt_template_id 分发到 entry_point 模块内的函数。

        C0 阶段仅支持 general_chat → chat()；其余 template_id 抛 NotImplementedError。
        """
        if not meta.entry_point:
            raise ValueError(
                f"llm_dispatch Skill 缺少 entry_point 字段: {meta.name}"
            )

        template_id = params.pop("prompt_template_id", None)
        if template_id is None:
            raise ValueError(
                f"llm_dispatch Skill 调用缺少 prompt_template_id 参数: {meta.name}"
            )

        func_name = self._LLM_DISPATCH_SUPPORTED.get(template_id)
        if func_name is None:
            raise NotImplementedError(
                f"llm_dispatch template_id='{template_id}' 尚未支持直接 invoke "
                f"(C0 仅支持 {list(self._LLM_DISPATCH_SUPPORTED.keys())}，"
                f"reason / review_portfolio / analyze_allocation / analyze_performance 待 C6)。"
            )

        module = importlib.import_module(meta.entry_point)
        func = getattr(module, func_name, None)
        if not callable(func):
            raise ValueError(
                f"entry_point 模块 '{meta.entry_point}' 中找不到函数 '{func_name}'"
            )

        return func(**params)

    def _invoke_prompt_inject(self, meta: SkillMeta, **params) -> str:
        """prompt_inject 类型：返回 SKILL.md 的 body 文本。"""
        return meta.body

    def _invoke_validation(self, meta: SkillMeta, **params) -> object:
        """validation 类型：调用 entry_point 指向的校验函数。"""
        if not meta.entry_point:
            raise ValueError(
                f"validation Skill 缺少 entry_point 字段: {meta.name}"
            )
        if ":" not in meta.entry_point:
            raise ValueError(
                f"entry_point 格式错误（期望 'module:func_name'）: {meta.entry_point}"
            )

        module_path, func_name = meta.entry_point.split(":", 1)
        module = importlib.import_module(module_path)
        func = getattr(module, func_name, None)

        if not callable(func):
            raise ValueError(
                f"entry_point 函数不存在: {meta.entry_point}"
            )
        return func(**params)


# 全局单例
_loader: Optional[SkillsLoader] = None
_loader_lock = threading.Lock()


def get_skills_loader() -> SkillsLoader:
    global _loader
    if _loader is None:
        with _loader_lock:
            if _loader is None:
                _loader = SkillsLoader()
    return _loader
