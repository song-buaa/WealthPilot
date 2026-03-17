"""
WealthPilot - CSV 导入模块
支持导入持仓数据和负债数据
列格式已更新为多货币版本。
"""

import csv
import io
from typing import List, Tuple, Optional
from app.models import Portfolio, Position, Liability, get_session, init_db


# CSV 列定义
POSITION_COLUMNS = [
    "平台", "资产名称", "代码", "大类", "头寸",
    "市值（美元）", "市值（港币）", "市值(人民币)",
    "盈亏(原始货币)", "盈亏(元)", "盈亏%", "segment",
]
LIABILITY_COLUMNS = ["负债名称", "类型", "用途", "金额(元)", "年利率"]

VALID_ASSET_CLASSES = {"货币", "固收", "权益", "另类", "衍生"}
VALID_SEGMENTS = {"投资", "养老", "公积金"}
VALID_PURPOSES = {"投资杠杆", "购房", "日常消费"}


def get_sample_position_csv() -> str:
    """生成持仓CSV模板示例"""
    header = ",".join(POSITION_COLUMNS)
    rows = [
        "老虎证券,理想汽车 LI,LI,权益,2500,43325,,298943,253943,-3.70,投资",
        "老虎证券,Meta META,META,权益,27,16495.92,,113922,103662,60.80,投资",
        "支付宝,余额宝,,货币,,,199,199,0,0,投资",
        "招商银行,朝朝宝,,货币,,,2285,2285,0,0,投资",
        "建设银行,企业年金（建行）,,货币,,,161012,161012,0,0,养老",
        "全国住房公积金,住房公积金（杭州）,,货币,,,359522,359522,0,0,公积金",
    ]
    return header + "\n" + "\n".join(rows)


def get_sample_liability_csv() -> str:
    """生成负债CSV模板示例"""
    header = ",".join(LIABILITY_COLUMNS)
    rows = [
        "招行-信用卡,信用卡,日常消费,5169,0",
        "农行-网捷贷,信用贷,投资杠杆,300000,3.0",
        "建行-快贷,信用贷,购房,140900,3.05",
    ]
    return header + "\n" + "\n".join(rows)


def _safe_float(value, default=0.0) -> float:
    """安全转换 float，空字符串或无效值返回 default"""
    if value is None:
        return default
    s = str(value).strip()
    if not s:
        return default
    try:
        return float(s)
    except ValueError:
        return default


def parse_positions_csv(csv_content: str) -> Tuple[List[dict], List[str]]:
    """
    解析持仓CSV内容
    返回: (解析后的持仓列表, 错误信息列表)
    """
    positions = []
    errors = []

    reader = csv.DictReader(io.StringIO(csv_content))

    for i, row in enumerate(reader, start=2):
        try:
            name = row.get("资产名称", "").strip()
            if not name:
                errors.append(f"第{i}行: 资产名称不能为空")
                continue

            asset_class = row.get("大类", "").strip()
            if asset_class not in VALID_ASSET_CLASSES:
                errors.append(f"第{i}行: 大类必须为 {'|'.join(VALID_ASSET_CLASSES)}，当前值: {asset_class}")
                continue

            segment = row.get("segment", "投资").strip() or "投资"
            if segment not in VALID_SEGMENTS:
                errors.append(f"第{i}行: segment 必须为 投资|养老|公积金，当前值: {segment}")
                continue

            usd_value = _safe_float(row.get("市值（美元）"))
            hkd_value = _safe_float(row.get("市值（港币）"))
            cny_value = _safe_float(row.get("市值(人民币)"))

            # 判断原始货币
            if usd_value > 0:
                original_currency = "USD"
                original_value = usd_value
            elif hkd_value > 0:
                original_currency = "HKD"
                original_value = hkd_value
            else:
                original_currency = "CNY"
                original_value = cny_value

            # 计算 fx_rate
            if original_currency == "CNY" or original_value == 0:
                fx_rate = 1.0
            elif cny_value > 0 and original_value > 0:
                fx_rate = cny_value / original_value
            else:
                fx_rate = 1.0

            positions.append({
                "name": name,
                "ticker": row.get("代码", "").strip(),
                "platform": row.get("平台", "其他").strip(),
                "asset_class": asset_class,
                "currency": original_currency,
                "quantity": _safe_float(row.get("头寸")),
                "cost_price": 0.0,
                "current_price": 0.0,
                "market_value_cny": cny_value,
                "original_currency": original_currency,
                "original_value": original_value,
                "fx_rate_to_cny": fx_rate,
                "fx_rate_date": "latest",
                "segment": segment,
                "profit_loss_original_value": _safe_float(row.get("盈亏(原始货币)")),
                "profit_loss_value": _safe_float(row.get("盈亏(元)")),
                "profit_loss_rate": _safe_float(row.get("盈亏%")),
            })
        except (ValueError, KeyError) as e:
            errors.append(f"第{i}行: 数据格式错误 - {str(e)}")

    return positions, errors


def parse_liabilities_csv(csv_content: str) -> Tuple[List[dict], List[str]]:
    """
    解析负债CSV内容
    返回: (解析后的负债列表, 错误信息列表)
    """
    liabilities = []
    errors = []

    reader = csv.DictReader(io.StringIO(csv_content))

    for i, row in enumerate(reader, start=2):
        try:
            name = row.get("负债名称", "").strip()
            if not name:
                errors.append(f"第{i}行: 负债名称不能为空")
                continue

            purpose = row.get("用途", "日常消费").strip() or "日常消费"
            if purpose not in VALID_PURPOSES:
                errors.append(f"第{i}行: 用途必须为 投资杠杆|购房|日常消费，当前值: {purpose}")
                continue

            liabilities.append({
                "name": name,
                "category": row.get("类型", "其他").strip(),
                "purpose": purpose,
                "amount": _safe_float(row.get("金额(元)")),
                "interest_rate": _safe_float(row.get("年利率")),
            })
        except (ValueError, KeyError) as e:
            errors.append(f"第{i}行: 数据格式错误 - {str(e)}")

    return liabilities, errors


def positions_to_csv(positions) -> str:
    """将持仓对象列表导出为 CSV 字符串"""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=POSITION_COLUMNS)
    writer.writeheader()
    for p in positions:
        writer.writerow({
            "平台": p.platform,
            "资产名称": p.name,
            "代码": p.ticker or "",
            "大类": p.asset_class,
            "头寸": p.quantity or "",
            "市值（美元）": p.original_value if p.original_currency == "USD" else "",
            "市值（港币）": p.original_value if p.original_currency == "HKD" else "",
            "市值(人民币)": p.market_value_cny,
            "盈亏(原始货币)": p.profit_loss_original_value or "",
            "盈亏(元)": p.profit_loss_value or "",
            "盈亏%": p.profit_loss_rate or "",
            "segment": p.segment or "投资",
        })
    return output.getvalue()


def liabilities_to_csv(liabilities) -> str:
    """将负债对象列表导出为 CSV 字符串"""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=LIABILITY_COLUMNS)
    writer.writeheader()
    for l in liabilities:
        writer.writerow({
            "负债名称": l.name,
            "类型": l.category,
            "用途": l.purpose or "日常消费",
            "金额(元)": l.amount,
            "年利率": l.interest_rate,
        })
    return output.getvalue()


def import_to_db(portfolio_id: int, positions: List[dict], liabilities: List[dict]) -> str:
    """
    将解析后的数据导入数据库
    如果portfolio已有数据，先清空再导入（全量覆盖）
    """
    session = get_session()
    try:
        portfolio = session.query(Portfolio).filter_by(id=portfolio_id).first()
        if not portfolio:
            return "投资组合不存在"

        # 清空旧数据
        session.query(Position).filter_by(portfolio_id=portfolio_id).delete()
        session.query(Liability).filter_by(portfolio_id=portfolio_id).delete()

        # 导入持仓
        for p in positions:
            pos = Position(portfolio_id=portfolio_id, **p)
            session.add(pos)

        # 导入负债
        for l in liabilities:
            liab = Liability(portfolio_id=portfolio_id, **l)
            session.add(liab)

        session.commit()
        return f"导入成功: {len(positions)} 条持仓, {len(liabilities)} 条负债"
    except Exception as e:
        session.rollback()
        return f"导入失败: {str(e)}"
    finally:
        session.close()


def ensure_default_portfolio() -> int:
    """确保存在一个默认投资组合，返回其ID"""
    init_db()
    session = get_session()
    try:
        portfolio = session.query(Portfolio).first()
        if not portfolio:
            portfolio = Portfolio(name="我的投资组合")
            session.add(portfolio)
            session.commit()
        return portfolio.id
    finally:
        session.close()
