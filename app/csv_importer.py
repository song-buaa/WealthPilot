"""
WealthPilot - CSV 导入模块
支持导入持仓数据和负债数据
"""

import csv
import io
from typing import List, Tuple, Optional
from app.models import Portfolio, Position, Liability, get_session, init_db


# CSV 模板列定义
POSITION_COLUMNS = ["资产名称", "代码", "平台", "大类资产", "币种", "数量", "成本价", "当前价格", "市值(人民币)"]
LIABILITY_COLUMNS = ["负债名称", "类型", "金额(人民币)", "年利率(%)"]


def get_sample_position_csv() -> str:
    """生成持仓CSV模板示例"""
    header = ",".join(POSITION_COLUMNS)
    rows = [
        "理想汽车,LI,港美股券商,权益,USD,500,25.00,32.00,112000",
        "腾讯控股,0700.HK,港美股券商,权益,HKD,200,320.00,380.00,86400",
        "沪深300ETF,510300,境内券商,权益,CNY,10000,4.20,4.50,45000",
        "招银理财稳健1号,,银行,固收,CNY,1,100000,102000,102000",
        "天弘余额宝,,支付宝,现金,CNY,1,50000,50200,50200",
        "易方达蓝筹精选,005827,支付宝,权益,CNY,20000,1.80,2.10,42000",
        "国债逆回购,,境内券商,固收,CNY,1,80000,80500,80500",
    ]
    return header + "\n" + "\n".join(rows)


def get_sample_liability_csv() -> str:
    """生成负债CSV模板示例"""
    header = ",".join(LIABILITY_COLUMNS)
    rows = [
        "招行信用卡,信用卡,15000,0",
        "微粒贷,信用贷,30000,14.4",
        "花呗,信用贷,5000,0",
    ]
    return header + "\n" + "\n".join(rows)


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

            asset_class = row.get("大类资产", "").strip()
            if asset_class not in ["权益", "固收", "现金", "另类"]:
                errors.append(f"第{i}行: 大类资产必须为 权益/固收/现金/另类，当前值: {asset_class}")
                continue

            market_value = float(row.get("市值(人民币)", 0))

            positions.append({
                "name": name,
                "ticker": row.get("代码", "").strip(),
                "platform": row.get("平台", "其他").strip(),
                "asset_class": asset_class,
                "currency": row.get("币种", "CNY").strip(),
                "quantity": float(row.get("数量", 0)),
                "cost_price": float(row.get("成本价", 0)),
                "current_price": float(row.get("当前价格", 0)),
                "market_value_cny": market_value,
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

            liabilities.append({
                "name": name,
                "category": row.get("类型", "其他").strip(),
                "amount": float(row.get("金额(人民币)", 0)),
                "interest_rate": float(row.get("年利率(%)", 0)),
            })
        except (ValueError, KeyError) as e:
            errors.append(f"第{i}行: 数据格式错误 - {str(e)}")

    return liabilities, errors


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
