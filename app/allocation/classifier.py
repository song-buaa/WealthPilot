"""
资产配置模块 — 资产归类规则

将持仓的 asset_class（中文）映射到配置模块的 AllocAssetClass 枚举。
处理混合基金等特殊情况。
"""

from app.allocation.types import AllocAssetClass, CN_TO_ALLOC


# 标准归类映射（资产类型描述 → 大类）
# 这些关键词会与持仓名称或标签匹配
KEYWORD_CLASSIFICATION = {
    # CASH
    "现金": AllocAssetClass.CASH,
    "活期": AllocAssetClass.CASH,
    "货币基金": AllocAssetClass.CASH,
    "余额宝": AllocAssetClass.CASH,
    "零钱通": AllocAssetClass.CASH,
    # FIXED_INCOME
    "纯债": AllocAssetClass.FIXED,
    "短债": AllocAssetClass.FIXED,
    "债券ETF": AllocAssetClass.FIXED,
    "美债": AllocAssetClass.FIXED,
    "国债": AllocAssetClass.FIXED,
    "可转债": AllocAssetClass.FIXED,  # V1 归固收
    # EQUITY
    "股票": AllocAssetClass.EQUITY,
    "权益基金": AllocAssetClass.EQUITY,
    "股票ETF": AllocAssetClass.EQUITY,
    "指数基金": AllocAssetClass.EQUITY,
    # ALTERNATIVE
    "黄金": AllocAssetClass.ALT,
    "REITs": AllocAssetClass.ALT,
    "大宗商品": AllocAssetClass.ALT,
    "原油": AllocAssetClass.ALT,
    # DERIVATIVE
    "期权": AllocAssetClass.DERIV,
    "期货": AllocAssetClass.DERIV,
    "结构性产品": AllocAssetClass.DERIV,
}

# 混合基金标签映射
MIXED_FUND_MAP = {
    "偏股混合": AllocAssetClass.EQUITY,
    "偏债混合": AllocAssetClass.FIXED,
}


def classify_by_asset_class_cn(asset_class_cn: str) -> AllocAssetClass:
    """
    根据持仓的中文 asset_class 字段归类。
    这是最常用的路径：Position.asset_class 直接是 "权益"/"固收"/"货币"/"另类"/"衍生"。
    """
    result = CN_TO_ALLOC.get(asset_class_cn)
    if result is not None:
        return result
    return AllocAssetClass.UNCLASSIFIED


def classify_by_name_or_tag(name: str, tags: str = "") -> AllocAssetClass:
    """
    备用路径：根据资产名称或标签关键词匹配归类。
    用于 asset_class 缺失或不标准的情况。
    """
    combined = f"{name} {tags}"

    # 先检查混合基金标签
    for label, cls in MIXED_FUND_MAP.items():
        if label in combined:
            return cls

    # 关键词匹配
    for keyword, cls in KEYWORD_CLASSIFICATION.items():
        if keyword in combined:
            return cls

    return AllocAssetClass.UNCLASSIFIED


def classify_position(asset_class_cn: str, name: str = "", tags: str = "") -> AllocAssetClass:
    """
    综合归类：优先用 asset_class 中文字段，不匹配时用名称/标签兜底。
    """
    result = classify_by_asset_class_cn(asset_class_cn)
    if result != AllocAssetClass.UNCLASSIFIED:
        return result
    return classify_by_name_or_tag(name, tags)
