"""
WealthPilot Skills Loader (v2.6 M8)

按 Anthropic Agent Skills 开放标准加载本地 SKILL.md 文件夹。

设计原则：
  - 启动时扫描 skills/ 目录，发现所有 SKILL.md
  - 解析 YAML frontmatter 得到 metadata（name / description / intent_binding 等）
  - 单例缓存，避免重复加载
  - 提供两个核心接口：
      get_skill_metadatas()：用于 LLM Selector 的渐进式披露
      load_skill_body()：用于实际执行时加载完整 body
"""
from __future__ import annotations

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
