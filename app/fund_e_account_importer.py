"""
基金E账户App Excel持仓导入器。

Excel格式(固定结构):
- 行0: 大标题
- 行1-3: 姓名/证件/空行
- 行4: 表头(序号/基金代码/基金名称/份额类别/基金管理人/基金账户/销售机构/交易账户/持有份额/份额日期/基金净值/净值日期/资产情况/结算币种/分红方式)
- 行5起: 数据,序号为整数的行是有效数据
- 末尾: 空行 + 说明行(序号非整数,自动跳过)

E账户不提供成本/盈亏 → cost_price=0, profit_loss_value=0, profit_loss_rate=0
"""
import pandas as pd

from backend.services.instruments.classification import (
    AssetClassificationEvidence,
    business_position_classification_fields,
    classify_instrument,
)

# 销售机构 → WealthPilot platform
SALES_CHANNEL_TO_PLATFORM = {
    "蚂蚁（杭州）基金销售": "支付宝",
    "珠海盈米基金销售": "盈米基金",
    "建设银行": "建设银行",
    "招商银行": "招商银行",
    "腾安基金销售（深圳）": "腾讯理财通",
    "中国工商银行": "工商银行",
    "中国银行": "中国银行",
    "农业银行": "农业银行",
    "交通银行": "交通银行",
    "平安银行": "平安银行",
    "京东肯特瑞基金销售": "京东金融",
}

DOMESTIC_FUND_PLATFORMS = set(SALES_CHANNEL_TO_PLATFORM.values())


def _normalize_platform(sales_channel) -> str:
    if not sales_channel or (isinstance(sales_channel, float) and pd.isna(sales_channel)):
        return "境内基金"
    ch = str(sales_channel).strip()
    return SALES_CHANNEL_TO_PLATFORM.get(ch, ch)


def _safe_float(val, default=0.0) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _classification_fields(fund_name: str) -> dict:
    """Classify a fund from evidence; unresolved funds remain UNKNOWN."""
    evidence = AssetClassificationEvidence(
        broker="fund_e_account",
        broker_security_type="FUND",
        stock_type="FUND",
        vehicle_type_hint="FUND",
        long_name=str(fund_name or ""),
        currency="CNY",
    )
    return business_position_classification_fields(
        classify_instrument(evidence), evidence=evidence
    )


def parse_fund_e_excel(file_path: str) -> dict[str, list[dict]]:
    """
    解析基金E账户Excel。
    返回: {platform: [position_dict, ...], ...}
    """
    df = pd.read_excel(file_path, sheet_name="持有信息", header=None)

    # 找表头行
    header_row_idx = None
    for i, row in df.iterrows():
        if str(row.iloc[0]).strip() == "序号":
            header_row_idx = i
            break
    if header_row_idx is None:
        raise ValueError("未找到表头行(含'序号'的行),请确认文件为基金E账户导出格式")

    df.columns = df.iloc[header_row_idx]
    df = df.iloc[header_row_idx + 1:].reset_index(drop=True)

    # 只保留序号是整数的有效行
    valid_rows = []
    for _, row in df.iterrows():
        seq = row.get("序号")
        if seq is None or (isinstance(seq, float) and pd.isna(seq)):
            continue
        try:
            int(float(str(seq)))
            valid_rows.append(row)
        except (ValueError, TypeError):
            continue

    # 找"资产情况"列(可能含换行符)
    asset_col = next((c for c in df.columns if c and "资产情况" in str(c)), None)

    result: dict[str, list[dict]] = {}

    for row in valid_rows:
        ticker = str(row.get("基金代码", "")).strip().zfill(6)
        name = str(row.get("基金名称", "")).strip()
        platform = _normalize_platform(row.get("销售机构"))
        quantity = _safe_float(row.get("持有份额"))
        current_price = _safe_float(row.get("基金净值"))
        market_value_cny = _safe_float(row.get(asset_col)) if asset_col else 0.0
        pos_dict = {
            "name": name,
            "ticker": ticker,
            **_classification_fields(name),
            "currency": "CNY",
            "quantity": quantity,
            "cost_price": 0.0,
            "current_price": current_price,
            "market_value_cny": market_value_cny,
            "original_currency": "CNY",
            "original_value": market_value_cny,
            "fx_rate_to_cny": 1.0,
            "profit_loss_value": 0.0,
            "profit_loss_rate": 0.0,
            "segment": "投资",
        }

        if platform not in result:
            result[platform] = []
        result[platform].append(pos_dict)

    return result
