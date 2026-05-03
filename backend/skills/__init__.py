from .loader import SkillsLoader, get_skills_loader, SkillMeta

__all__ = [
    "SkillsLoader",
    "get_skills_loader",
    "SkillMeta",
    "invoke_skill",
]


def invoke_skill(skill_name: str, **params) -> object:
    """
    快捷调用 Skill：内部走单例 SkillsLoader.invoke。

    用法：
        from backend.skills import invoke_skill
        result = invoke_skill("wp-fetch-holdings", portfolio_id=1)
    """
    return get_skills_loader().invoke(skill_name, **params)
