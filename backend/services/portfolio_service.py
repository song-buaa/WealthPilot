"""
Portfolio Service — 账户总览业务逻辑

从 app_pages/overview.py 提取的纯业务逻辑，去除所有 Streamlit 依赖。
直接复用：app.analyzer, app.csv_importer, app.bank_screenshot, app.platform_importers
"""

from __future__ import annotations

from typing import Optional

from app.models import Portfolio, Position, Liability, get_session
from app.analyzer import analyze_portfolio, check_deviations, BalanceSheet
from app.csv_importer import (
    parse_positions_csv,
    parse_liabilities_csv,
    import_to_db,
    positions_to_csv,
    liabilities_to_csv,
    get_sample_position_csv,
    get_sample_liability_csv,
)
from app.bank_screenshot import (
    parse_bank_screenshot,
    bank_positions_to_db,
    parse_broker_screenshot,
    broker_positions_to_db,
)
from app.platform_importers import parse_tiger_csv, parse_futu_csv

import logging as _ps_logging
_ps_logger = _ps_logging.getLogger(__name__)


# ── 券商账户现金（Tiger 专用）──────────────────────────────────────────────────

def _get_tiger_account_cash() -> tuple[float, list[dict]]:
    """
    从 Tiger get_prime_assets() 取分币种现金，各自折 CNY。

    数据源: segments["S"].currency_assets 的 cash_balance（对齐 APP「预估现金额」口径）。
    跳过 cash_balance == 0 的币种。

    Returns:
        (total_cny, per_currency_details)
        per_currency_details: [{"currency": "HKD", "cash_balance": 15257.97,
                                "cny_value": 14189.91, "fx_rate_to_cny": 0.93}, ...]
    """
    try:
        from backend.core.demo_mode import PUBLIC_DEMO_MODE
        if PUBLIC_DEMO_MODE:
            # demo 模式：种子 CSV 中货币基金代表现金类，不需要额外 Tiger 现金行
            # 种子 CSV 已有广发货币A(50000) + 工银货币A(30000) = ¥80,000 货币类
            return 0.0, []

        import os as _os_tiger
        tiger_id = _os_tiger.getenv("TIGER_ID")
        account = _os_tiger.getenv("TIGER_ACCOUNT")
        pk_path = _os_tiger.getenv("TIGER_PRIVATE_KEY_PATH")

        if not all([tiger_id, account, pk_path]):
            return 0.0, []

        from tigeropen.tiger_open_config import TigerOpenClientConfig
        from tigeropen.trade.trade_client import TradeClient
        from tigeropen.common.consts import Language

        with open(pk_path) as f:
            pk_pem = f.read()
        pk_raw = (
            pk_pem
            .replace("-----BEGIN RSA PRIVATE KEY-----", "")
            .replace("-----END RSA PRIVATE KEY-----", "")
            .strip()
        )

        config = TigerOpenClientConfig(sandbox_debug=False)
        config.private_key = pk_raw
        config.tiger_id = tiger_id
        config.account = account
        config.language = Language.zh_CN

        client = TradeClient(config)
        pa = client.get_prime_assets(account=account)

        seg_s = pa.segments.get("S")
        if seg_s is None:
            return 0.0, []

        currency_assets = getattr(seg_s, "currency_assets", None)
        if not currency_assets:
            return 0.0, []

        from app.fx_service import FXService
        fx = FXService()

        total_cny = 0.0
        details = []
        for currency, ca in currency_assets.items():
            cash_bal = getattr(ca, "cash_balance", 0.0) or 0.0
            if cash_bal == 0.0:
                continue
            cny_value, fx_rate, _ = fx.convert(cash_bal, currency, "CNY")
            details.append({
                "currency": currency,
                "cash_balance": cash_bal,
                "cny_value": round(cny_value, 2),
                "fx_rate_to_cny": round(fx_rate, 6),
            })
            total_cny += cny_value

        total_cny = round(total_cny, 2)

        _ps_logger.info(
            f"[券商现金] Tiger 分币种: "
            + ", ".join(f"{d['currency']} {d['cash_balance']:.2f}→CNY {d['cny_value']:.2f}" for d in details)
            + f" | 合计 CNY {total_cny:.2f}"
        )
        return total_cny, details

    except Exception as e:
        _ps_logger.warning(f"[券商现金] Tiger 获取失败（不阻断）: {e}")
        return 0.0, []


def _make_tiger_cash_item(total_cny: float, details: list[dict]) -> dict:
    """构造「老虎账户现金」虚拟持仓行（方案 A：单行合计）。"""
    return {
        "id":                  -1,
        "name":                "老虎账户现金",
        "ticker":              None,
        "platform":            "老虎证券",
        "asset_class":         "货币",
        "currency":            "CNY",
        "quantity":            None,
        "cost_price":          None,
        "current_price":       None,
        "market_value_cny":    total_cny,
        "original_currency":   None,
        "original_value":      None,
        "fx_rate_to_cny":      None,
        "profit_loss_value":   0.0,
        "profit_loss_rate":    0.0,
        "segment":             "投资",
        "_cash_details":       details,  # 保留按币种明细，方便切到方案 B
    }


# ── 总览数据 ──────────────────────────────────────────────────────────────────

def get_summary(portfolio_id: int) -> dict:
    """资产总览（对应 BalanceSheet），含在途预估现金额。"""
    bs = analyze_portfolio(portfolio_id)
    if bs is None:
        return {"error": "portfolio not found"}

    # 注入券商账户现金到货币大类和总资产
    tiger_cash_cny, _cash_details = _get_tiger_account_cash()
    if tiger_cash_cny > 0:
        bs.monetary_value += tiger_cash_cny
        bs.total_assets += tiger_cash_cny
        bs.net_worth = bs.total_assets - bs.total_liabilities
        # 重算占比
        if bs.total_assets > 0:
            bs.equity_pct = round(bs.equity_value / bs.total_assets * 100, 1)
            bs.fixed_income_pct = round(bs.fixed_income_value / bs.total_assets * 100, 1)
            bs.monetary_pct = round(bs.monetary_value / bs.total_assets * 100, 1)
            bs.alternative_pct = round(bs.alternative_value / bs.total_assets * 100, 1)
            bs.derivative_pct = round(bs.derivative_value / bs.total_assets * 100, 1)
            bs.leverage_ratio = round(bs.total_liabilities / bs.total_assets * 100, 1)
            for name in bs.concentration:
                # concentration 已经是百分比，需要基于新 total_assets 重算
                pass  # concentration 是 analyzer 里基于旧 total 算的，这里不重算避免复杂化

    return {
        "total_assets": bs.total_assets,
        "total_liabilities": bs.total_liabilities,
        "net_worth": bs.net_worth,
        "leverage_ratio": bs.leverage_ratio,
        "total_profit_loss": bs.total_profit_loss,
        "allocation": {
            "equity":       {"value": bs.equity_value,        "pct": bs.equity_pct},
            "fixed_income": {"value": bs.fixed_income_value,  "pct": bs.fixed_income_pct},
            "monetary":     {"value": bs.monetary_value,      "pct": bs.monetary_pct},
            "alternative":  {"value": bs.alternative_value,   "pct": bs.alternative_pct},
            "derivative":   {"value": bs.derivative_value,    "pct": bs.derivative_pct},
        },
        "platform_distribution": bs.platform_distribution,
        "concentration": bs.concentration,
    }


def get_positions(portfolio_id: int, segment: Optional[str] = None) -> dict:
    """持仓列表，可按 segment 过滤。含在途预估现金额虚拟行。"""
    session = get_session()
    try:
        q = session.query(Position).filter_by(portfolio_id=portfolio_id)
        if segment:
            q = q.filter_by(segment=segment)
        positions = q.all()
        items = [_position_to_dict(p) for p in positions]

        # 注入券商账户现金虚拟行（仅 segment 为空或"投资"时）
        if segment is None or segment == "投资":
            tiger_cash_cny, cash_details = _get_tiger_account_cash()
            if tiger_cash_cny > 0:
                items.append(_make_tiger_cash_item(tiger_cash_cny, cash_details))

        return {"items": items, "total": len(items)}
    finally:
        session.close()


def get_liabilities(portfolio_id: int) -> dict:
    """负债列表"""
    session = get_session()
    try:
        liabilities = session.query(Liability).filter_by(portfolio_id=portfolio_id).all()
        items = [_liability_to_dict(l) for l in liabilities]
        return {"items": items, "total": len(items)}
    finally:
        session.close()


def get_alerts(portfolio_id: int) -> dict:
    """偏差预警列表"""
    bs = analyze_portfolio(portfolio_id)
    if bs is None:
        return {"items": [], "count": 0}

    alerts = check_deviations(portfolio_id, bs)
    items = [
        {
            "alert_type": a.alert_type,
            "severity":   a.severity,
            "title":      a.title,
            "description": a.description,
            "current_value": a.current_value,
            "target_value":  a.target_value,
            "deviation":     a.deviation,
        }
        for a in alerts
    ]
    return {"items": items, "count": len(items)}


# ── 导入 ──────────────────────────────────────────────────────────────────────

def import_from_csv(
    file_bytes: bytes,
    portfolio_id: int,
    content_type: str = "positions",  # "positions" | "liabilities"
) -> dict:
    """
    从 CSV 文件字节导入持仓或负债。
    全量覆盖模式：先清空同类数据再写入。
    返回 {"imported": N, "errors": [...]}
    """
    try:
        csv_content = file_bytes.decode("utf-8-sig")  # 兼容 Excel BOM
    except UnicodeDecodeError:
        csv_content = file_bytes.decode("gbk", errors="replace")

    if content_type == "liabilities":
        parsed, errors = parse_liabilities_csv(csv_content)
        if errors:
            return {"imported": 0, "errors": errors}
        _overwrite_liabilities(portfolio_id, parsed)
        return {"imported": len(parsed), "errors": []}
    else:
        parsed, errors = parse_positions_csv(csv_content)
        if errors:
            return {"imported": 0, "errors": errors}
        _overwrite_positions(portfolio_id, parsed)
        return {"imported": len(parsed), "errors": []}


def import_from_broker_csv(
    file_bytes: bytes,
    broker: str,
    portfolio_id: int,
) -> dict:
    """
    老虎证券 / 富途证券 CSV 导入（按平台替换）。
    broker: "老虎证券" | "富途证券"
    返回 {"imported": N, "rate": float, "errors": [...]}
    """
    try:
        csv_content = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        csv_content = file_bytes.decode("gbk", errors="replace")

    try:
        if broker == "老虎证券":
            positions, rate = parse_tiger_csv(csv_content)
        elif broker == "富途证券":
            positions, rate = parse_futu_csv(csv_content)
        else:
            return {"imported": 0, "rate": 0.0, "errors": [f"不支持的券商: {broker}"]}
    except Exception as e:
        return {"imported": 0, "rate": 0.0, "errors": [f"解析失败: {str(e)}"]}

    if not positions:
        return {"imported": 0, "rate": 0.0, "errors": ["未能解析到持仓数据，请检查文件格式"]}

    _replace_positions_by_platform(portfolio_id, broker, positions)
    return {"imported": len(positions), "rate": rate, "errors": []}


def import_from_screenshot(
    image_bytes: bytes,
    platform: str,
    portfolio_id: int,
) -> dict:
    """
    银行/券商截图解析导入。
    platform 示例: "招商银行" | "支付宝" | "建设银行" | "雪盈证券" | "国金证券"
    返回 {"imported": N, "positions": [...], "error": str|None}
    """
    broker_platforms = {"雪盈证券", "国金证券"}

    if platform in broker_platforms:
        raw, error = parse_broker_screenshot(image_bytes, platform)
        if error:
            return {"imported": 0, "positions": [], "error": error}
        position_dicts = broker_positions_to_db(raw, platform)
    else:
        raw, error = parse_bank_screenshot(image_bytes, platform)
        if error:
            return {"imported": 0, "positions": [], "error": error}
        position_dicts = bank_positions_to_db(raw, platform)

    if not position_dicts:
        return {"imported": 0, "positions": [], "error": "未从截图中识别到有效数据"}

    # 写入 DB：按 name+platform 更新，不存在则新建
    _upsert_positions_by_name(portfolio_id, platform, position_dicts)
    return {"imported": len(position_dicts), "positions": position_dicts, "error": None}


def clear_positions(portfolio_id: int) -> None:
    """清空所有持仓（保留负债）"""
    session = get_session()
    try:
        session.query(Position).filter_by(portfolio_id=portfolio_id).delete()
        session.commit()
    finally:
        session.close()


# ── CSV 模板下载 ──────────────────────────────────────────────────────────────

def get_position_csv_template() -> str:
    return get_sample_position_csv()


def get_liability_csv_template() -> str:
    return get_sample_liability_csv()


def export_positions_csv(portfolio_id: int) -> str:
    session = get_session()
    try:
        positions = session.query(Position).filter_by(portfolio_id=portfolio_id).all()
        return positions_to_csv(positions)
    finally:
        session.close()


def export_liabilities_csv(portfolio_id: int) -> str:
    session = get_session()
    try:
        liabilities = session.query(Liability).filter_by(portfolio_id=portfolio_id).all()
        return liabilities_to_csv(liabilities)
    finally:
        session.close()


# ── 内部辅助 ──────────────────────────────────────────────────────────────────

def _position_to_dict(p: Position) -> dict:
    return {
        "id":                  p.id,
        "name":                p.name,
        "ticker":              p.ticker,
        "platform":            p.platform,
        "asset_class":         p.asset_class,
        "currency":            p.currency,
        "quantity":            p.quantity,
        "cost_price":          p.cost_price,
        "current_price":       p.current_price,
        "market_value_cny":    p.market_value_cny,
        "original_currency":   p.original_currency,
        "original_value":      p.original_value,
        "fx_rate_to_cny":      p.fx_rate_to_cny,
        "profit_loss_value":   p.profit_loss_value,
        "profit_loss_rate":    p.profit_loss_rate,
        "segment":             p.segment,
    }


def _liability_to_dict(l: Liability) -> dict:
    return {
        "id":            l.id,
        "name":          l.name,
        "category":      l.category,
        "purpose":       l.purpose,
        "amount":        l.amount,
        "interest_rate": l.interest_rate,
    }


def _overwrite_positions(portfolio_id: int, position_dicts: list[dict]) -> None:
    """全量覆盖持仓（清空再写入）"""
    session = get_session()
    try:
        session.query(Position).filter_by(portfolio_id=portfolio_id).delete()
        for d in position_dicts:
            session.add(Position(portfolio_id=portfolio_id, **d))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _overwrite_liabilities(portfolio_id: int, liability_dicts: list[dict]) -> None:
    """全量覆盖负债（清空再写入）"""
    session = get_session()
    try:
        session.query(Liability).filter_by(portfolio_id=portfolio_id).delete()
        for d in liability_dicts:
            session.add(Liability(portfolio_id=portfolio_id, **d))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _replace_positions_by_platform(
    portfolio_id: int,
    platform: str,
    position_dicts: list[dict],
) -> None:
    """按平台全量替换持仓（删除该平台所有旧记录，写入新记录）"""
    session = get_session()
    try:
        session.query(Position).filter_by(
            portfolio_id=portfolio_id, platform=platform
        ).delete()
        for d in position_dicts:
            session.add(Position(
                portfolio_id=portfolio_id,
                platform=platform,
                segment="投资",
                **{k: v for k, v in d.items() if k not in ("platform", "portfolio_id", "segment")},
            ))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _upsert_positions_by_name(
    portfolio_id: int,
    platform: str,
    position_dicts: list[dict],
) -> None:
    """
    按 name+platform 更新已有持仓，不存在则新建。
    截图导入场景：只更新 market_value_cny 等字段，不清空其他持仓。
    """
    session = get_session()
    try:
        for d in position_dicts:
            name = d.get("name", "")
            existing = (
                session.query(Position)
                .filter_by(portfolio_id=portfolio_id, name=name, platform=platform)
                .first()
            )
            if existing:
                for k, v in d.items():
                    if hasattr(existing, k):
                        setattr(existing, k, v)
            else:
                new_pos = Position(
                    portfolio_id=portfolio_id,
                    platform=platform,
                    currency="CNY",
                    original_currency="CNY",
                    segment="投资",
                    **{k: v for k, v in d.items() if k not in ("platform", "portfolio_id", "segment", "currency", "original_currency")},
                )
                session.add(new_pos)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
