# backend/agents/skill_reconcile.py
#
# v3.8.1 对账层：比对 PlanningOutput.selected_skills 与 ExecutionOutput.invoked_skills
# 的 Executing 阶段子集差异。纯函数，无副作用，不修改任何入参。

from dataclasses import dataclass, field
from .observed_skill_phase_map import SKILL_PHASE, PSEUDO_SKILLS


@dataclass
class ReconcileReport:
    route: str
    declared_exec: list[str] = field(default_factory=list)
    invoked_exec: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    declared_not_invoked: list[str] = field(default_factory=list)
    invoked_not_declared: list[str] = field(default_factory=list)
    pseudo_observed: list[str] = field(default_factory=list)
    unknown_declared: list[str] = field(default_factory=list)
    unknown_invoked: list[str] = field(default_factory=list)
    is_consistent: bool = True
    has_unknown: bool = False


def reconcile_executing_skills(route, selected_skills, invoked_skills) -> ReconcileReport:
    """纯函数。只比对 Executing 阶段。不修改任何入参。对 None 入参鲁棒。"""
    selected_skills = selected_skills or []
    invoked_skills = invoked_skills or []
    route = route or ""

    # 分母:声明且归属 executing
    declared_exec = {s for s in selected_skills if SKILL_PHASE.get(s) == "executing"}

    invoked_set = set(invoked_skills)
    pseudo = invoked_set & PSEUDO_SKILLS
    invoked_real = invoked_set - PSEUDO_SKILLS
    # 分子:实跑且归属 executing
    invoked_exec = {s for s in invoked_real if SKILL_PHASE.get(s) == "executing"}

    # unknown 兜底:出现在清单/实跑里、但映射表完全没覆盖的 wp-* skill
    unknown_declared = {s for s in selected_skills if s.startswith("wp-") and s not in SKILL_PHASE}
    unknown_invoked = {s for s in invoked_real if s.startswith("wp-") and s not in SKILL_PHASE}

    matched = declared_exec & invoked_exec
    declared_not_invoked = declared_exec - invoked_exec
    invoked_not_declared = invoked_exec - declared_exec

    is_consistent = (not declared_not_invoked) and (not invoked_not_declared)
    has_unknown = bool(unknown_declared or unknown_invoked)

    return ReconcileReport(
        route=route,
        declared_exec=sorted(declared_exec),
        invoked_exec=sorted(invoked_exec),
        matched=sorted(matched),
        declared_not_invoked=sorted(declared_not_invoked),
        invoked_not_declared=sorted(invoked_not_declared),
        pseudo_observed=sorted(pseudo),
        unknown_declared=sorted(unknown_declared),
        unknown_invoked=sorted(unknown_invoked),
        is_consistent=is_consistent,
        has_unknown=has_unknown,
    )
