"""
WealthPilot - 投资账户总览页面
策略：以 ui_preview.html 的 HTML/CSS/JS 为前端展示层原样保留，
      使用 st.components.v1.html() 嵌入，Python 负责计算数据并注入。
"""

import json
import re
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

from app.models import Portfolio, Position, Liability, get_session
from app.analyzer import analyze_portfolio, check_deviations
from app.state import portfolio_id, get_position_count
from app.discipline.config import get_rules as _get_discipline_rules
from app.csv_importer import (
    parse_positions_csv, parse_liabilities_csv,
    positions_to_csv, liabilities_to_csv,
)

# ── 颜色常量（与 ui_preview.html 完全一致）──────────────────────────────────
ASSET_CHART_COLORS = {
    "货币": "#3B82F6", "固收": "#10B981", "权益": "#F59E0B",
    "另类": "#8B5CF6", "衍生": "#EF4444",
}
CHART_PALETTE = [
    "#3B82F6", "#10B981", "#F59E0B", "#8B5CF6", "#EF4444",
    "#06B6D4", "#84CC16", "#F97316",
]
def _clean_asset_name(name: str) -> str:
    """标准化资产名称：去除括号内容及 -W_N 类后缀。
    示例: '理想汽车 (LI)' -> '理想汽车'
          'Meta Platforms, Inc. (META)' -> 'Meta Platforms, Inc.'
          '理想汽车-W_1' -> '理想汽车'
    """
    # 去掉中英文括号及其内容
    name = re.sub(r'\s*[\(（][^)）]*[\)）]', '', name)
    # 去掉 -W_数字 后缀（H股命名惯例，如 理想汽车-W_1）
    name = re.sub(r'\s*-W_\d+$', '', name)
    return name.strip()


PLATFORM_TYPE_MAP = {
    "老虎证券": "overseas", "富途证券": "overseas", "雪盈证券": "overseas",
    "国金证券": "domestic",
    "建设银行": "bank",    "招商银行": "bank",
    "支付宝":   "thirdparty",
}


# ══════════════════════════════════════════════════════════════════════════════
# HTML 构建器 — 将真实数据注入 ui_preview.html 模板
# ══════════════════════════════════════════════════════════════════════════════

def _build_overview_html(bs, positions: list, alerts: list, portfolio, liabilities: list = None) -> str:
    """
    以 ui_preview.html 的 CSS/结构为基础（原样保留），注入 Python 计算的真实数据。
    侧边栏由 Streamlit 原生处理，HTML 中只保留主内容区。
    """
    if liabilities is None:
        liabilities = []

    # ── KPI 计算 ──────────────────────────────────────────────────────────────
    pnl = bs.total_profit_loss
    pnl_sign = "+" if pnl >= 0 else ""
    pnl_cls  = "pos" if pnl >= 0 else "neg"

    cost = bs.total_assets - pnl
    return_rate_html = ""
    if cost > 0:
        rr = pnl / cost * 100
        rr_sign = "+" if rr >= 0 else ""
        rr_cls   = "pos" if rr >= 0 else "neg"
        arrow    = "↑" if rr >= 0 else "↓"
        return_rate_html = (
            f'<div class="kpi-delta {rr_cls}">{arrow} 收益率 {rr_sign}{rr:.1f}%</div>'
        )

    net_assets     = bs.total_assets - bs.total_liabilities
    leverage_mult  = bs.total_assets / max(net_assets, 1)
    if portfolio and portfolio.max_leverage_ratio:
        # max_leverage_ratio 以小数存储（如 0.5 = 50% 负债率）→ 转换为倍数上限
        max_lev_mult  = 1.0 / max(1.0 - portfolio.max_leverage_ratio, 0.01)
        leverage_over = leverage_mult > max_lev_mult
    else:
        leverage_over = False
        max_lev_mult  = None
    leverage_cls = "alert" if leverage_over else ""
    # ── 杠杆分级 ──────────────────────────────────────────────────────────────
    if leverage_mult < 1.5:
        lev_icon   = "🟢"
        lev_label  = "安全（低风险）"
        lev_tip    = "当前杠杆水平较低，风险可控"
        lev_color  = "#059669"
    elif leverage_mult < 2.0:
        lev_icon   = "🟡"
        lev_label  = "可控（适度杠杆）"
        lev_tip    = "已使用杠杆，建议控制仓位集中度"
        lev_color  = "#D97706"
    elif leverage_mult < 2.5:
        lev_icon   = "🟠"
        lev_label  = "警戒（偏高）"
        lev_tip    = "杠杆偏高，需关注市场波动与回撤风险"
        lev_color  = "#EA580C"
    elif leverage_mult < 3.0:
        lev_icon   = "🔴"
        lev_label  = "高风险"
        lev_tip    = "杠杆较高，建议降低仓位或增加安全垫"
        lev_color  = "#DC2626"
    else:
        lev_icon   = "🔴"
        lev_label  = "危险（爆仓风险高）"
        lev_tip    = "杠杆过高，存在较大爆仓风险"
        lev_color  = "#DC2626"
    leverage_sub = (
        f'<div style="font-size:11px;font-weight:600;color:{lev_color};margin-top:4px">'
        f'{lev_icon} {lev_label}</div>'
    )
    leverage_tip = lev_tip

    # ── 偏差视图 ──────────────────────────────────────────────────────────────
    _r9   = _get_discipline_rules()["asset_allocation_ranges"]
    total = bs.total_assets or 1.0
    cash_min = _r9["monetary_min_amount"] / total * 100
    cash_max = _r9["monetary_max_amount"] / total * 100

    # ── 大类 tooltip：名字示例 + 点位金额 ──────────────────────────────────────
    _CLASS_EXAMPLES = {
        "货币": "余额宝、货币基金、活期存款等",
        "固收": "债券基金、银行理财、信托等",
        "权益": "股票、股票基金、指数ETF等",
        "另类": "黄金、大宗商品、REITs 等",
        "衍生": "期权、期货等",
    }
    _cls_val = {
        "货币": bs.monetary_value, "固收": bs.fixed_income_value,
        "权益": bs.equity_value,   "另类": bs.alternative_value,
        "衍生": bs.derivative_value,
    }

    def _wan(v):
        w = v / 10000
        return f"{w:.0f}" if w == int(w) else f"{w:.1f}"

    _cash_amt_min = _r9["monetary_min_amount"]
    _cash_amt_max = _r9["monetary_max_amount"]
    dev_cats = [
        {"name": "货币", "cur": bs.monetary_pct,
         "min": cash_min, "max": cash_max, "color": "#3B82F6",
         "range_tip": f"目标区间：{_wan(_cash_amt_min)}~{_wan(_cash_amt_max)} 万元"},
        {"name": "固收", "cur": bs.fixed_income_pct,
         "min": _r9["fixed_income_min"] * 100, "max": _r9["fixed_income_max"] * 100, "color": "#10B981",
         "range_tip": f"目标区间：{_r9['fixed_income_min']*100:.0f}% ~ {_r9['fixed_income_max']*100:.0f}%"},
        {"name": "权益", "cur": bs.equity_pct,
         "min": _r9["equity_min"] * 100, "max": _r9["equity_max"] * 100, "color": "#F59E0B",
         "range_tip": f"目标区间：{_r9['equity_min']*100:.0f}% ~ {_r9['equity_max']*100:.0f}%"},
        {"name": "另类", "cur": bs.alternative_pct,
         "min": 0, "max": _r9["alternatives_max"] * 100, "color": "#8B5CF6",
         "range_tip": f"目标区间：0% ~ {_r9['alternatives_max']*100:.0f}%"},
        {"name": "衍生", "cur": bs.derivative_pct,
         "min": 0, "max": _r9["derivatives_max"] * 100, "color": "#EF4444",
         "range_tip": f"目标区间：0% ~ {_r9['derivatives_max']*100:.0f}%"},
    ]
    deviation_items_html = ""
    for c in dev_cats:
        mn, mx, cur, color = c["min"], c["max"], c["cur"], c["color"]
        mid  = (mn + mx) / 2
        bw   = max(mx - mn, 0.5)
        dot  = min(max(cur, 0), 100)
        if cur > mx:
            badge = f'<span class="dev-badge over">↑ 超配 &nbsp;+{cur-mx:.1f}%</span>'
        elif mn > 0 and cur < mn:
            badge = f'<span class="dev-badge under">↓ 低配 &nbsp;−{mn-cur:.1f}%</span>'
        else:
            badge = '<span class="dev-badge ok">✓ 区间内</span>'
        # tooltip 内容
        _name_tip  = _CLASS_EXAMPLES.get(c["name"], "")
        _amt       = int(_cls_val.get(c["name"], 0) or 0)
        _amt_tip   = f"¥{_amt:,}"
        _range_tip = c["range_tip"]
        deviation_items_html += f"""
          <div class="deviation-item">
            <div class="dev-name" data-tip="{_name_tip}">{c["name"]}</div>
            <div class="dev-bar-wrap">
              <div class="dev-bar-range" data-tip="{_range_tip}" style="left:{mn:.1f}%;width:{bw:.1f}%"></div>
              <div class="dev-bar-mid"   style="left:{mid:.1f}%"></div>
              <div class="dev-bar-dot"   data-tip="{_amt_tip}" style="left:{dot:.1f}%;background:{color}"></div>
            </div>
            <div class="dev-current">{cur:.1f}%</div>
            <div>{badge}</div>
          </div>"""

    # ── ECharts 平台分布数据 ───────────────────────────────────────────────────
    platform_dist  = bs.platform_distribution or {}
    plat_sorted    = sorted(platform_dist.items(), key=lambda x: -x[1])
    plat_total_mv  = sum(platform_dist.values()) or 1.0

    echarts_data   = json.dumps([
        {"value": int(mv), "name": name,
         "itemStyle": {"color": CHART_PALETTE[i % len(CHART_PALETTE)]}}
        for i, (name, mv) in enumerate(plat_sorted)
    ])
    echarts_legend = json.dumps({
        name: f"{mv/plat_total_mv*100:.1f}%"
        for name, mv in plat_sorted
    })

    # ── 资产明细表格行 ────────────────────────────────────────────────────────
    plat_totals = {}
    for p in positions:
        plat_totals[p.platform] = plat_totals.get(p.platform, 0) + p.market_value_cny

    sorted_pos = sorted(
        positions,
        key=lambda p: (-plat_totals.get(p.platform, 0), -p.market_value_cny)
    )

    table_rows_html = ""
    for p in sorted_pos:
        ptype = PLATFORM_TYPE_MAP.get(p.platform, "overseas")
        ptag  = f'<span class="tag tag-{ptype}">{p.platform}</span>'
        ctag  = f'<span class="tag tag-class">{p.asset_class}</span>'

        usd  = f"${p.original_value:,.0f}" if p.original_currency == "USD" and p.original_value else "—"
        hkd  = f"HK${p.original_value:,.0f}" if p.original_currency == "HKD" and p.original_value else "—"
        cny  = f"¥{p.market_value_cny:,.0f}" if p.market_value_cny else "—"
        qty  = f"{int(p.quantity):,}" if p.quantity else "—"
        pct  = f"{bs.concentration.get(f'{p.id}:{p.name}', 0):.2f}%"

        v = p.profit_loss_value
        pnl_td = (
            '<td class="r td-zero">¥0</td>' if not v or v == 0 else
            f'<td class="r td-pos">+¥{v:,.0f}</td>' if v > 0 else
            f'<td class="r td-neg">-¥{abs(v):,.0f}</td>'
        )
        # 与 position_aggregator 保持一致：profit / cost（标准公式），不读 DB 存储的 profit_loss_rate
        _pnl_v = p.profit_loss_value or 0.0
        _cost_v = (p.market_value_cny or 0.0) - _pnl_v
        r = (_pnl_v / _cost_v * 100) if _cost_v > 0 else 0.0
        rate_td = (
            '<td class="r td-zero">0.00%</td>' if r == 0 else
            f'<td class="r td-pos">+{r:.2f}%</td>' if r > 0 else
            f'<td class="r td-neg">{r:.2f}%</td>'
        )

        table_rows_html += f"""
            <tr>
              <td>{ptag}</td>
              <td class="td-name" title="{p.name}">{_clean_asset_name(p.name)}</td>
              <td class="td-code">{p.ticker or "—"}</td>
              <td style="text-align:center">{ctag}</td>
              <td class="r">{qty}</td>
              <td class="r">{usd}</td>
              <td class="r">{hkd}</td>
              <td class="r td-mv">{cny}</td>
              <td class="r td-pct">{pct}</td>
              {pnl_td}
              {rate_td}
            </tr>"""

    # ════════════════════════════════════════════════════════════════════════
    # 完整 HTML — CSS 原样来自 ui_preview.html，仅移除侧边栏 + 调整 body
    # ════════════════════════════════════════════════════════════════════════
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
/* ═══════════════════════════════════════════
   Design Tokens — 原样来自 ui_preview.html
═══════════════════════════════════════════ */
:root {{
  --ocean-900: #0F1E35;
  --ocean-800: #1B2A4A;
  --ocean-700: #243558;
  --ocean-600: #2D4A7A;
  --ocean-50:  #F4F6FA;
  --blue-500:  #3B82F6;
  --blue-200:  #BFDBFE;
  --blue-100:  #DBEAFE;
  --green-600: #16A34A;
  --green-100: #DCFCE7;
  --red-600:   #DC2626;
  --red-100:   #FEE2E2;
  --amber-500: #F59E0B;
  --amber-100: #FEF3C7;
  --gray-700:  #374151;
  --gray-500:  #6B7280;
  --gray-400:  #9CA3AF;
  --gray-200:  #E5E7EB;
  --gray-100:  #F3F4F6;
  --white:     #FFFFFF;
  --shadow-sm: 0 1px 3px rgba(15,30,53,0.07), 0 1px 2px rgba(15,30,53,0.04);
  --shadow-dark: 0 6px 20px rgba(15,30,53,0.28);
  --radius: 12px;
}}

*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

/* 嵌入适配：去掉 height:100vh/overflow:hidden，改为可滚动 */
body {{
  font-family: 'PingFang SC', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, sans-serif;
  background: var(--ocean-50);
  color: var(--gray-700);
  font-size: 14px;
  -webkit-font-smoothing: antialiased;
}}

.main-wrap {{ display: block; }}

.content-left {{
  padding: 2px 28px 0 28px;
  min-width: 0;
}}

/* ═══════════════════════════════════════════
   页面标题 — 原样
═══════════════════════════════════════════ */
.page-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }}
.page-header-icon {{
  width: 38px; height: 38px; border-radius: 10px;
  background: linear-gradient(135deg, var(--ocean-800), var(--ocean-600));
  display: flex; align-items: center; justify-content: center; font-size: 17px;
}}
.page-title    {{ font-size: 20px; font-weight: 700; color: var(--ocean-800); letter-spacing: -0.3px; }}
.page-subtitle {{ font-size: 12px; color: var(--gray-400); margin-top: 1px; }}

/* ═══════════════════════════════════════════
   卡片 — 原样
═══════════════════════════════════════════ */
.card {{
  background: var(--white); border: 1px solid var(--gray-200);
  border-radius: var(--radius); padding: 20px; box-shadow: var(--shadow-sm);
}}
.card-title {{
  font-size: 13px; font-weight: 600; color: var(--gray-700);
  display: flex; align-items: center; gap: 6px; margin-bottom: 16px;
}}
.card-title-badge {{ margin-left: auto; font-size: 11px; font-weight: 400; color: var(--gray-400); }}

/* ═══════════════════════════════════════════
   KPI 区 — 原样 2fr 1fr 1fr
═══════════════════════════════════════════ */
.kpi-row {{ display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 12px; margin-bottom: 16px; }}
.kpi-primary {{
  background: linear-gradient(135deg, var(--ocean-800) 0%, var(--ocean-900) 100%);
  border-radius: var(--radius); padding: 20px 24px; box-shadow: var(--shadow-dark);
  display: flex; flex-direction: column; justify-content: space-between; min-height: 100px;
}}
.kpi-primary-label {{ font-size: 11px; font-weight: 600; color: rgba(255,255,255,0.48); text-transform: uppercase; letter-spacing: 0.6px; }}
.kpi-primary-value {{ font-size: 28px; font-weight: 700; color: #fff; letter-spacing: -1px; font-variant-numeric: tabular-nums; margin: 4px 0 8px; line-height: 1.1; }}
.kpi-primary-sub {{ display: flex; gap: 20px; }}
.kpi-primary-sub-item {{ font-size: 13px; color: rgba(255,255,255,0.55); }}
.kpi-primary-sub-item strong {{ color: rgba(255,255,255,0.9); font-weight: 600; }}
.kpi-secondary {{
  background: var(--white); border: 1px solid var(--gray-200); border-radius: var(--radius);
  padding: 16px 18px; box-shadow: var(--shadow-sm);
  display: flex; flex-direction: column; justify-content: space-between; min-height: 100px;
}}
.kpi-secondary-label {{ font-size: 11px; font-weight: 600; color: var(--gray-400); text-transform: uppercase; letter-spacing: 0.5px; }}
.kpi-secondary-value {{ font-size: 20px; font-weight: 700; color: var(--ocean-800); letter-spacing: -0.5px; font-variant-numeric: tabular-nums; margin-top: 4px; }}
.kpi-secondary-value.pos {{ color: var(--red-600); }}
.kpi-secondary-value.neg {{ color: var(--green-600); }}
.kpi-delta {{ font-size: 11px; font-weight: 600; margin-top: 4px; display: flex; align-items: center; gap: 3px; }}
.kpi-delta.pos  {{ color: var(--red-600); }}
.kpi-delta.neg  {{ color: var(--green-600); }}
.kpi-delta.warn {{ color: var(--amber-500); }}
.kpi-tertiary {{
  background: var(--white); border: 1px solid var(--gray-200); border-radius: var(--radius);
  padding: 16px 18px; box-shadow: var(--shadow-sm);
  display: flex; flex-direction: column; justify-content: space-between; min-height: 100px;
}}
.kpi-tertiary-label {{ font-size: 11px; font-weight: 500; color: var(--gray-400); text-transform: uppercase; letter-spacing: 0.5px; }}
.kpi-tertiary-value {{ font-size: 18px; font-weight: 600; color: var(--gray-700); letter-spacing: -0.3px; font-variant-numeric: tabular-nums; margin-top: 4px; }}
.kpi-tertiary-value.alert {{ color: var(--red-600); }}

/* ═══════════════════════════════════════════
   图表区 — 原样 3fr 2fr
═══════════════════════════════════════════ */
.chart-row {{ display: grid; grid-template-columns: 3fr 2fr; gap: 16px; margin-bottom: 16px; }}

/* ═══════════════════════════════════════════
   偏差视图 — 原样
═══════════════════════════════════════════ */
.deviation-list {{ padding: 0; }}
.deviation-item {{ display: grid; grid-template-columns: 52px 1fr 64px 104px; align-items: center; gap: 12px; height: 44px; border-bottom: 1px solid var(--gray-100); }}
.deviation-item:last-child {{ border-bottom: none; }}
.dev-name {{ font-size: 13px; font-weight: 500; color: var(--gray-700); }}
.dev-bar-wrap {{ position: relative; height: 7px; background: var(--gray-100); border-radius: 4px; }}
.dev-bar-range {{ position: absolute; top: 0; bottom: 0; background: rgba(59,130,246,0.12); border-radius: 4px; }}
.dev-bar-dot {{ position: absolute; top: 50%; transform: translate(-50%, -50%); width: 11px; height: 11px; border-radius: 50%; border: 2px solid var(--white); box-shadow: 0 0 0 1px rgba(0,0,0,0.12); z-index: 2; }}
.dev-bar-mid {{ position: absolute; top: -2px; bottom: -2px; width: 2px; background: rgba(59,130,246,0.35); border-radius: 1px; }}
.dev-current {{ font-size: 13px; font-weight: 600; color: var(--ocean-800); font-variant-numeric: tabular-nums; text-align: right; }}
.dev-badge {{ display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; border-radius: 5px; font-size: 11px; font-weight: 600; justify-content: center; white-space: nowrap; }}
.dev-badge.over  {{ background: var(--red-100);   color: var(--red-600); }}
.dev-badge.under {{ background: var(--blue-100);  color: #1D4ED8; }}
.dev-badge.ok    {{ background: var(--green-100); color: var(--green-600); }}
/* ── 通用 data-tip tooltip ── */
.cls-tip {{ position: fixed; z-index: 9999; background: #1F2937; color: #F9FAFB;
  border-radius: 6px; padding: 5px 10px; font-size: 12px; font-weight: 500;
  white-space: nowrap; pointer-events: none; display: none;
  box-shadow: 0 3px 10px rgba(0,0,0,0.18); }}
.cls-tip.show {{ display: block; }}

/* ═══════════════════════════════════════════
   表格 — 原样
═══════════════════════════════════════════ */
.data-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
.data-table thead th {{ padding: 8px 10px; font-size: 11px; font-weight: 600; color: var(--gray-400); text-transform: uppercase; letter-spacing: 0.4px; border-bottom: 1px solid var(--gray-200); white-space: nowrap; background: var(--white); }}
.data-table thead th.r {{ text-align: right; }}
.data-table tbody td {{ padding: 9px 10px; border-bottom: 1px solid var(--gray-100); vertical-align: middle; white-space: nowrap; }}
.data-table tbody td.r {{ text-align: right; }}
.data-table tbody tr:last-child td {{ border-bottom: none; }}
.data-table tbody tr:hover td {{ background: #F8FAFC; }}
.td-name {{ font-weight: 500; color: var(--ocean-800); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 0; }}
.td-code {{ color: var(--gray-400); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 0; }}
.td-mv   {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
.td-pct  {{ color: var(--gray-500); font-size: 12px; }}
.td-pos  {{ color: var(--red-600);   font-weight: 600; font-variant-numeric: tabular-nums; }}
.td-neg  {{ color: var(--green-600); font-weight: 600; font-variant-numeric: tabular-nums; }}
.td-zero {{ color: var(--gray-400);  font-variant-numeric: tabular-nums; }}
.tag {{ display: inline-flex; align-items: center; border-radius: 5px; padding: 2px 6px; font-size: 11px; font-weight: 600; white-space: nowrap; }}
.tag-overseas   {{ background: var(--blue-100);  color: #1D4ED8; }}
.tag-domestic   {{ background: var(--amber-100); color: #92400E; }}
.tag-bank       {{ background: var(--green-100); color: #065F46; }}
.tag-thirdparty {{ background: #EDE9FE; color: #5B21B6; }}
.tag-class      {{ background: var(--gray-100);  color: var(--gray-700); margin-left: 3px; }}

/* ═══════════════════════════════════════════
   风险告警 — 原样
═══════════════════════════════════════════ */
.alert-item {{ display: flex; gap: 10px; padding: 12px 14px; background: #FFF5F5; border: 1px solid #FECACA; border-radius: 10px; margin-bottom: 8px; }}
.alert-item:last-child {{ margin-bottom: 0; }}
.alert-icon  {{ font-size: 15px; flex-shrink: 0; margin-top: 1px; }}
.alert-title {{ font-size: 13px; font-weight: 600; color: #991B1B; }}
.alert-body  {{ font-size: 12px; color: #B91C1C; margin-top: 3px; line-height: 1.5; }}

/* AI 报告 — 原样 */
.ai-section {{ background: linear-gradient(135deg, #EFF6FF 0%, #F0FDF4 100%); border: 1px solid var(--blue-200); border-radius: var(--radius); padding: 20px; margin-top: 0; }}
.ai-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }}
.ai-title  {{ font-size: 14px; font-weight: 700; color: var(--ocean-800); }}
.ai-badge  {{ background: var(--red-100); color: var(--red-600); border-radius: 5px; padding: 2px 8px; font-size: 11px; font-weight: 600; }}
.ai-desc   {{ font-size: 12px; color: var(--gray-500); margin-bottom: 14px; line-height: 1.6; }}
.btn-primary {{
  background: linear-gradient(135deg, var(--blue-500), #1D4ED8);
  color: #fff; border: none; border-radius: 8px; padding: 9px 20px;
  font-size: 13px; font-weight: 600; cursor: pointer; transition: opacity 0.15s;
  box-shadow: 0 2px 8px rgba(59,130,246,0.3);
}}
.btn-primary:hover {{ opacity: 0.9; }}

::-webkit-scrollbar {{ width: 4px; height: 4px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{ background: var(--gray-200); border-radius: 2px; }}
</style>
</head>
<body>

<div class="main-wrap">

  <!-- ═══ 主内容区（左） ═══ -->
  <div class="content-left">

    <!-- 页面标题 -->
    <div class="page-header">
      <div class="page-header-icon">📊</div>
      <div>
        <div class="page-title">投资账户总览</div>
        <div class="page-subtitle">Investment Portfolio Overview</div>
      </div>
    </div>

    <!-- KPI 区 -->
    <div class="kpi-row">
      <div class="kpi-primary">
        <div class="kpi-primary-label">总资产（投资）</div>
        <div class="kpi-primary-value">¥{bs.total_assets:,.0f}</div>
        <div class="kpi-primary-sub">
          <div class="kpi-primary-sub-item">净资产 <strong>¥{bs.net_worth:,.0f}</strong></div>
          <div class="kpi-primary-sub-item">负债 <strong>¥{bs.total_liabilities:,.0f}</strong></div>
        </div>
      </div>
      <div class="kpi-secondary">
        <div class="kpi-secondary-label">浮动盈亏</div>
        <div class="kpi-secondary-value {pnl_cls}">{pnl_sign}¥{pnl:,.0f}</div>
        {return_rate_html}
      </div>
      <div class="kpi-tertiary" data-tip="{leverage_tip}" style="cursor:default">
        <div class="kpi-tertiary-label">杠杆倍数</div>
        <div class="kpi-tertiary-value {leverage_cls}">{leverage_mult:.2f}x</div>
        {leverage_sub}
      </div>
    </div>

    <!-- 图表区 -->
    <div class="chart-row">

      <!-- 大类资产配置偏差视图 -->
      <div class="card" style="padding:16px 20px 8px">
        <div class="card-title" style="margin-bottom:10px">
          📊 大类资产配置
          <span style="font-size:11px;font-weight:400;color:var(--gray-400);margin-left:2px">当前 vs 目标区间</span>
        </div>
        <div style="display:flex;gap:14px;margin-bottom:8px;font-size:11px;color:var(--gray-400)">
          <div style="display:flex;align-items:center;gap:5px">
            <div style="width:18px;height:7px;background:rgba(59,130,246,0.14);border-radius:3px;border:1px solid rgba(59,130,246,0.28)"></div>目标区间
          </div>
          <div style="display:flex;align-items:center;gap:5px">
            <div style="width:11px;height:11px;border-radius:50%;background:#3B82F6;border:2px solid white;box-shadow:0 0 0 1px rgba(0,0,0,0.12)"></div>当前配置
          </div>
          <div style="display:flex;align-items:center;gap:5px">
            <div style="width:2px;height:11px;background:rgba(59,130,246,0.45);border-radius:1px"></div>目标中值
          </div>
        </div>
        <div class="deviation-list">
          {deviation_items_html}
        </div>
      </div>

      <!-- 平台分布（ECharts 环形图） -->
      <div class="card" style="padding:16px 14px 8px">
        <div class="card-title" style="margin-bottom:10px">🏦 平台分布</div>
        <div id="chartPlatform" style="height:230px"></div>
      </div>

    </div>

    <!-- 资产明细表格 -->
    <div class="card" style="padding:20px 20px 16px;margin-bottom:16px">
      <div class="card-title">
        📋 资产明细
        <span class="card-title-badge">{len(positions)} 只持仓</span>
      </div>
      <div style="max-height:494px;overflow-y:auto;border-radius:6px;">
        <table class="data-table" style="table-layout:fixed;width:100%">
          <colgroup>
            <col style="width:72px">
            <col style="width:120px">
            <col style="width:64px">
            <col style="width:60px">
            <col style="width:48px">
            <col style="width:80px">
            <col style="width:80px">
            <col style="width:88px">
            <col style="width:52px">
            <col style="width:88px">
            <col style="width:48px">
          </colgroup>
          <thead>
            <tr>
              <th style="position:sticky;top:0;z-index:1;background:var(--white)">平台</th>
              <th style="position:sticky;top:0;z-index:1;background:var(--white)">资产名称</th>
              <th style="position:sticky;top:0;z-index:1;background:var(--white)">资产代码</th>
              <th style="position:sticky;top:0;z-index:1;background:var(--white);text-align:center">资产大类</th>
              <th class="r" style="position:sticky;top:0;z-index:1;background:var(--white)">头寸</th>
              <th class="r" style="position:sticky;top:0;z-index:1;background:var(--white)">市值(美元)</th>
              <th class="r" style="position:sticky;top:0;z-index:1;background:var(--white)">市值(港币)</th>
              <th class="r" style="position:sticky;top:0;z-index:1;background:var(--white)">市值(人民币)</th>
              <th class="r" style="position:sticky;top:0;z-index:1;background:var(--white)">占比%</th>
              <th class="r" style="position:sticky;top:0;z-index:1;background:var(--white)">盈亏(人民币)</th>
              <th class="r" style="position:sticky;top:0;z-index:1;background:var(--white)">盈亏%</th>
            </tr>
          </thead>
          <tbody>
            {table_rows_html}
          </tbody>
        </table>
      </div>
    </div>

  </div><!-- /content-left -->

</div><!-- /main-wrap -->

<div id="clsTip" class="cls-tip"></div>
<script>
// ── 通用 data-tip tooltip ──────────────────────────────────────────────────
(function() {{
  const box = document.getElementById('clsTip');
  document.addEventListener('mouseover', ev => {{
    const el = ev.target.closest('[data-tip]');
    if (!el) {{ box.classList.remove('show'); return; }}
    box.textContent = el.dataset.tip;
    box.classList.add('show');
    move(ev);
  }});
  document.addEventListener('mousemove', ev => {{
    if (box.classList.contains('show')) move(ev);
  }});
  document.addEventListener('mouseout', ev => {{
    if (!ev.relatedTarget || !ev.relatedTarget.closest('[data-tip]'))
      box.classList.remove('show');
  }});
  function move(ev) {{
    const w = box.offsetWidth || 160;
    const h = box.offsetHeight || 32;
    let x = ev.clientX + 14;
    let y = ev.clientY - h - 8;
    if (x + w > window.innerWidth - 8)  x = ev.clientX - w - 14;
    if (y < 8) y = ev.clientY + 16;
    box.style.left = x + 'px';
    box.style.top  = y + 'px';
  }}
}})();
</script>

<script>
// ECharts 环形图 — 原样来自 ui_preview.html，数据替换为真实值
const _data   = {echarts_data};
const _legMap = {echarts_legend};
const chart   = echarts.init(document.getElementById('chartPlatform'));
chart.setOption({{
  tooltip: {{
    trigger: 'item',
    backgroundColor: '#fff', borderColor: '#E5E7EB', borderWidth: 1,
    padding: [8, 12], textStyle: {{ color: '#1F2937', fontSize: 12 }},
    formatter: '{{b}}<br/>市值: <b>¥{{c}}</b><br/>占比: <b>{{d}}%</b>',
  }},
  legend: {{
    orient: 'vertical', right: 0, top: 'middle',
    textStyle: {{ color: '#6B7280', fontSize: 11 }},
    itemWidth: 8, itemHeight: 8, itemGap: 8,
    formatter: n => `${{n}}  ${{_legMap[n] || ''}}`,
  }},
  series: [{{
    type: 'pie',
    radius: ['44%', '70%'],
    center: ['36%', '50%'],
    avoidLabelOverlap: false,
    label: {{ show: false }},
    emphasis: {{
      label: {{ show: false }},
      itemStyle: {{ shadowBlur: 6, shadowColor: 'rgba(0,0,0,0.08)' }},
    }},
    data: _data,
  }}],
}});
window.addEventListener('resize', () => chart.resize());

// ── 自动调整 iframe 高度 ─────────────────────────────────────────────
(function() {{
  function autoFit() {{
    const h = document.documentElement.scrollHeight + 8;
    if (window.frameElement) window.frameElement.style.height = h + 'px';
  }}
  function injectExpStyle() {{
    // 已删除 — 样式由父页面 st.markdown #wp-import-panel 单一注入
  }}
  window.addEventListener('load', function() {{
    autoFit();
    setTimeout(autoFit, 300);
  }});
}})();
</script>
</body>
</html>"""


def _build_liab_table_html(bs, liabilities: list = None) -> str:
    """构建负债明细表格 HTML"""
    if liabilities is None: liabilities = []
    inv_liabilities = [lb for lb in liabilities if lb.purpose == "投资杠杆"]
    liab_rows_html = ""
    total_liab = sum(lb.amount for lb in inv_liabilities) or 1.0
    for lb in sorted(inv_liabilities, key=lambda x: -x.amount):
        pct_l = lb.amount / total_liab * 100
        rate_str = f"{lb.interest_rate:.2f}%" if lb.interest_rate else "—"
        liab_rows_html += f"""
            <tr>
              <td class="td-name">{lb.name}</td>
              <td>{lb.category}</td>
              <td>{lb.purpose}</td>
              <td class="r td-mv">¥{lb.amount:,.0f}</td>
              <td class="r td-pct">{pct_l:.1f}%</td>
              <td class="r">{rate_str}</td>
            </tr>"""
    if not liab_rows_html:
        liab_rows_html = '<tr><td colspan="6" style="text-align:center;color:var(--gray-400);padding:20px">暂无负债数据</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
:root {{
  --ocean-800: #1B2A4A;
  --ocean-50:  #F4F6FA;
  --gray-700:  #374151;
  --gray-500:  #6B7280;
  --gray-400:  #9CA3AF;
  --gray-200:  #E5E7EB;
  --gray-100:  #F3F4F6;
  --white:     #FFFFFF;
  --shadow-sm: 0 1px 3px rgba(15,30,53,0.07), 0 1px 2px rgba(15,30,53,0.04);
  --radius: 12px;
}}
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'PingFang SC', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, sans-serif;
  background: var(--ocean-50);
  color: var(--gray-700);
  font-size: 14px;
  -webkit-font-smoothing: antialiased;
}}
.content-left {{ padding: 0 28px 0 28px; }}
.card {{
  background: var(--white); border: 1px solid var(--gray-200);
  border-radius: var(--radius); padding: 20px; box-shadow: var(--shadow-sm);
}}
.card-title {{
  font-size: 13px; font-weight: 600; color: var(--gray-700);
  display: flex; align-items: center; gap: 6px; margin-bottom: 16px;
}}
.card-title-badge {{ margin-left: auto; font-size: 11px; font-weight: 400; color: var(--gray-400); }}
.data-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
.data-table thead th {{ padding: 8px 10px; font-size: 11px; font-weight: 600; color: var(--gray-400); text-transform: uppercase; letter-spacing: 0.4px; border-bottom: 1px solid var(--gray-200); white-space: nowrap; background: var(--white); }}
.data-table thead th.r {{ text-align: right; }}
.data-table tbody td {{ padding: 9px 10px; border-bottom: 1px solid var(--gray-100); vertical-align: middle; white-space: nowrap; }}
.data-table tbody td.r {{ text-align: right; }}
.data-table tbody tr:last-child td {{ border-bottom: none; }}
.data-table tbody tr:hover td {{ background: #F8FAFC; }}
.td-name {{ font-weight: 500; color: var(--ocean-800); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 0; }}
.td-mv   {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
.td-pct  {{ color: var(--gray-500); font-size: 12px; }}
</style>
</head>
<body>
	<div class="content-left">
	    <div class="card" style="padding:20px 20px 16px;margin-top:20px;margin-bottom:0">
	      <div class="card-title">
        🏦 负债明细
        <span class="card-title-badge">¥{bs.total_liabilities:,.0f}</span>
      </div>
      <div style="max-height:494px;overflow-y:auto;border-radius:6px;">
        <table class="data-table" style="table-layout:fixed;width:100%">
          <colgroup>
            <col style="width:auto">
            <col style="width:80px">
            <col style="width:80px">
            <col style="width:120px">
            <col style="width:60px">
            <col style="width:80px">
          </colgroup>
          <thead>
            <tr>
              <th style="position:sticky;top:0;z-index:1;background:var(--white)">负债名称</th>
              <th style="position:sticky;top:0;z-index:1;background:var(--white)">类型</th>
              <th style="position:sticky;top:0;z-index:1;background:var(--white)">用途</th>
              <th class="r" style="position:sticky;top:0;z-index:1;background:var(--white)">金额(人民币)</th>
              <th class="r" style="position:sticky;top:0;z-index:1;background:var(--white)">占比</th>
              <th class="r" style="position:sticky;top:0;z-index:1;background:var(--white)">年利率</th>
            </tr>
          </thead>
          <tbody>
            {liab_rows_html}
          </tbody>
        </table>
      </div>
    </div>
</div>
<script>
(function() {{
  function autoFit() {{
    const h = document.documentElement.scrollHeight + 8;
    if (window.frameElement) window.frameElement.style.height = h + 'px';
  }}
  window.addEventListener('load', function() {{
    autoFit();
    setTimeout(autoFit, 300);
  }});
}})();
</script>
</body>
</html>"""

def _build_alerts_ai_html(bs, alerts: list) -> str:
    """构建风险告警 + AI 报告 HTML"""
    hi = [a for a in alerts if a.severity == "高"]
    mi = [a for a in alerts if a.severity == "中"]
    imp_alerts = hi + mi
    alerts_html = ""
    if imp_alerts:
        cnt_label   = f"{len(hi)} 项高风险" if hi else f"{len(mi)} 项中风险"
        badge_color = "var(--red-600)" if hi else "var(--amber-500)"
        items_html  = ""
        for i, a in enumerate(imp_alerts):
            icon  = "🔴" if a.severity == "高" else "🟡"
            extra = ' style="margin-bottom:0"' if i == len(imp_alerts) - 1 else ""
            items_html += f"""
      <div class="alert-item"{extra}>
        <div class="alert-icon">{icon}</div>
        <div>
          <div class="alert-title">[{a.alert_type}] {a.title}</div>
          <div class="alert-body">{a.description}</div>
        </div>
      </div>"""
        alerts_html = f"""
    <div class="card" style="border-color:#FECACA;background:#FFFBFB;margin-top:0;margin-bottom:20px">
      <div class="card-title" style="color:#991B1B">
        🔴 风险告警
        <span class="card-title-badge" style="color:{badge_color}">{cnt_label}</span>
      </div>
      {items_html}
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
:root {{
  --ocean-800: #1B2A4A;
  --ocean-50:  #F4F6FA;
  --blue-500:  #3B82F6;
  --blue-200:  #BFDBFE;
  --green-600: #16A34A;
  --green-100: #DCFCE7;
  --red-600:   #DC2626;
  --red-100:   #FEE2E2;
  --amber-500: #F59E0B;
  --amber-100: #FEF3C7;
  --gray-700:  #374151;
  --gray-500:  #6B7280;
  --gray-400:  #9CA3AF;
  --gray-200:  #E5E7EB;
  --gray-100:  #F3F4F6;
  --white:     #FFFFFF;
  --shadow-sm: 0 1px 3px rgba(15,30,53,0.07), 0 1px 2px rgba(15,30,53,0.04);
  --radius: 12px;
}}
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'PingFang SC', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, sans-serif;
  background: var(--ocean-50);
  color: var(--gray-700);
  font-size: 14px;
  -webkit-font-smoothing: antialiased;
}}
.content-left {{ padding: 0 28px 24px 28px; }}
.card {{
  background: var(--white); border: 1px solid var(--gray-200);
  border-radius: var(--radius); padding: 20px; box-shadow: var(--shadow-sm);
}}
.card-title {{
  font-size: 13px; font-weight: 600; color: var(--gray-700);
  display: flex; align-items: center; gap: 6px; margin-bottom: 16px;
}}
.card-title-badge {{ margin-left: auto; font-size: 11px; font-weight: 400; color: var(--gray-400); }}
.alert-item {{ display: flex; gap: 10px; padding: 12px 14px; background: #FFF5F5; border: 1px solid #FECACA; border-radius: 10px; margin-bottom: 8px; }}
.alert-item:last-child {{ margin-bottom: 0; }}
.alert-icon  {{ font-size: 15px; flex-shrink: 0; margin-top: 1px; }}
.alert-title {{ font-size: 13px; font-weight: 600; color: #991B1B; }}
.alert-body  {{ font-size: 12px; color: #B91C1C; margin-top: 3px; line-height: 1.5; }}
</style>
</head>
<body>
<div class="content-left">
    {alerts_html}
</div>
<script>
(function() {{
  function autoFit() {{
    const h = document.documentElement.scrollHeight + 8;
    if (window.frameElement) window.frameElement.style.height = h + 'px';
  }}
  window.addEventListener('load', function() {{
    autoFit();
    setTimeout(autoFit, 300);
  }});
}})();
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════════

def render():
    # ── 数据检查 ─────────────────────────────────────────────────────────────
    position_count = get_position_count(portfolio_id)
    if position_count == 0:
        st.info("暂无持仓数据，请先通过下方「导入资产数据」上传 CSV 文件。")
        _render_import_panel(portfolio_id)
        return

    bs = analyze_portfolio(portfolio_id)
    if not bs:
        st.error("分析失败，请检查数据。")
        return

    session = get_session()
    try:
        portfolio   = session.query(Portfolio).filter_by(id=portfolio_id).first()
        positions   = session.query(Position).filter_by(
            portfolio_id=portfolio_id, segment="投资"
        ).all()
        liabilities = session.query(Liability).filter_by(portfolio_id=portfolio_id).all()
    finally:
        session.close()

    alerts = check_deviations(portfolio_id, bs)

    n_alerts   = len([a for a in alerts if a.severity in ("高", "中")])
    n_inv_liab = len([lb for lb in liabilities if lb.purpose == "投资杠杆"])

    # ── 注入导入面板样式（在 iframe 渲染前写入 DOM，确保每次生效）────────────
    # ── 注入导入面板样式（单一来源，作用域限定在 #wp-import-panel）────────────
    # 说明：此处是 expander 样式的唯一注入点。
    # 已删除顶部 iframe 内的 injectExpStyle() JS 函数（双重注入来源之一），
    # 消除了两套 CSS 选择器数值冲突（border-radius 16px vs 12px 等）。
    # 已废弃 [stLayoutWrapper]:has([stExpander]) { margin-bottom: -16px } hack，
    # 改由 expander 容器自身的 margin-top/bottom 控制垂直节奏。
    st.markdown("""
<style>
/* ══════════════════════════════════════════════════════
   导入/导出面板 — 唯一样式来源（v2.0 紧凑版）
   设计目标：消除双层卡片感、压缩垂直空间、轻量化 Uploader
   下载按钮绝对定位到 Tabs 行右上角
══════════════════════════════════════════════════════ */

/* ── 统一垂直间距系统 (Vertical Spacing System v3.0) ── */
/* 1. 彻底清除主内容区默认垂直间距 (Gap)，但不影响侧边栏 */
[data-testid="stMainView"] [data-testid="stVerticalBlock"] {
  gap: 0 !important;
}

/* 2. 强制消除所有 stHtml (iframe) 容器自带的边距 */
[data-testid="stMainView"] [data-testid="stHtml"] {
  margin-bottom: 0 !important;
  padding-bottom: 0 !important;
}

/* 3. 强制消除 stMarkdown 容器自带的边距 */
[data-testid="stMainView"] [data-testid="stMarkdownContainer"] p {
  margin-bottom: 0 !important;
}
[data-testid="stMainView"] .stMarkdown {
  margin-bottom: 0 !important;
}

/* 4. 恢复模块内部内容区的间距 (Tabs/Container 内部) */
[data-testid="stLayoutWrapper"] [data-testid="stVerticalBlock"] {
  gap: 8px !important;
}

/* 5. 导入模块卡片间距控制 (Section Gap = 20px) */
/* 资产导入模块：底部留出 20px 标准间距 */
[data-testid="stElementContainer"]:has(#wp-import-card-anchor) + [data-testid="stElementContainer"] {
  margin-bottom: 20px !important;
}
/* 负债导入模块：底部留出 20px 标准间距 */
[data-testid="stElementContainer"]:has(#wp-liab-import-card-anchor) + [data-testid="stElementContainer"] {
  margin-bottom: 20px !important;
}

/* ── anchor EC：高度为 0，不占空间 ── */
[data-testid="stElementContainer"]:has(#wp-import-card-anchor),
[data-testid="stElementContainer"]:has(#wp-liab-import-card-anchor),
[data-testid="stElementContainer"]:has(#wp-import-container-anchor),
[data-testid="stElementContainer"]:has(#wp-import-body-anchor),
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor),
[data-testid="stElementContainer"]:has(#wp-import-dl-anchor),
[data-testid="stElementContainer"]:has(#wp-liab-import-container-anchor),
[data-testid="stElementContainer"]:has(#wp-liab-import-body-anchor),
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor),
[data-testid="stElementContainer"]:has(#wp-liab-import-dl-anchor) {
  height: 0 !important;
  overflow: visible !important;
  margin: 0 !important;
  padding: 0 !important;
}

/* ── 按钮 EC：左缩进 28px + 宽度自适应 ── */
[data-testid="stElementContainer"]:has(#wp-import-card-anchor) + [data-testid="stElementContainer"],
[data-testid="stElementContainer"]:has(#wp-liab-import-card-anchor) + [data-testid="stElementContainer"] {
  margin-left: 28px !important;
  width: calc(100% - 56px) !important;
}

/* ── 头部按钮：卡片头部外观 ── */
[data-testid="stElementContainer"]:has(#wp-import-card-anchor) + [data-testid="stElementContainer"] button,
[data-testid="stElementContainer"]:has(#wp-liab-import-card-anchor) + [data-testid="stElementContainer"] button {
  background: #ffffff !important;
  border: 1px solid #E5E7EB !important;
  border-radius: 12px !important;
  box-shadow: 0 1px 3px rgba(15,30,53,0.07), 0 1px 2px rgba(15,30,53,0.04) !important;
  color: #1E3A5F !important;
  font-size: 13px !important;
  font-weight: 600 !important;
  letter-spacing: 0.1px !important;
  padding: 0 20px !important;
  height: 56px !important;
  min-height: 56px !important;
  text-align: left !important;
  justify-content: flex-start !important;
  display: flex !important;
  align-items: center !important;
  cursor: pointer !important;
  transition: background .12s, border-color .12s !important;
  width: 100% !important;
  white-space: nowrap !important;
  overflow: hidden !important;
}
/* 强制覆盖头部按钮内部 p 标签的字号 */
[data-testid="stElementContainer"]:has(#wp-import-card-anchor) + [data-testid="stElementContainer"] button p,
[data-testid="stElementContainer"]:has(#wp-liab-import-card-anchor) + [data-testid="stElementContainer"] button p {
  font-size: 13px !important;
  font-weight: 600 !important;
}
[data-testid="stElementContainer"]:has(#wp-import-card-anchor) + [data-testid="stElementContainer"] button:hover,
[data-testid="stElementContainer"]:has(#wp-liab-import-card-anchor) + [data-testid="stElementContainer"] button:hover {
  background: #F9FAFB !important;
  border-color: #D1D5DB !important;
  box-shadow: 0 2px 6px rgba(15,30,53,0.10) !important;
}
/* 展开状态：头部去掉下方圆角，与内容区无缝拼合 */
[data-testid="stVerticalBlock"]:has(#wp-import-card-anchor):has([data-testid="stLayoutWrapper"]) > [data-testid="stElementContainer"]:nth-child(2) button,
[data-testid="stVerticalBlock"]:has(#wp-liab-import-card-anchor):has([data-testid="stLayoutWrapper"]) > [data-testid="stElementContainer"]:nth-child(2) button {
  border-radius: 12px 12px 0 0 !important;
  border-bottom: 1px solid #F3F4F6 !important;
}

/* ── 内容区 stLayoutWrapper：紧凑 padding，无双层卡片 ── */
[data-testid="stElementContainer"]:has(#wp-import-card-anchor) ~ [data-testid="stLayoutWrapper"],
[data-testid="stElementContainer"]:has(#wp-liab-import-card-anchor) ~ [data-testid="stLayoutWrapper"] {
  margin-top: -32px !important;
  margin-left: 28px !important;
  width: calc(100% - 56px) !important;
  background: #ffffff !important;
  border-top: 0px none !important;
  border-right: 1px solid #E5E7EB !important;
  border-bottom: 1px solid #E5E7EB !important;
  border-left: 1px solid #E5E7EB !important;
  border-radius: 0 0 12px 12px !important;
  box-shadow: 0 1px 3px rgba(15,30,53,0.07), 0 1px 2px rgba(15,30,53,0.04) !important;
  padding: 10px 16px 12px !important;
  position: relative !important;  /* 为下载按钮绝对定位提供 containing block */
}
/* 内层 stVerticalBlock：压缩元素间距 */
[data-testid="stElementContainer"]:has(#wp-import-card-anchor) ~ [data-testid="stLayoutWrapper"] [data-testid="stVerticalBlock"],
[data-testid="stElementContainer"]:has(#wp-liab-import-card-anchor) ~ [data-testid="stLayoutWrapper"] [data-testid="stVerticalBlock"] {
  gap: 8px !important;
}

/* ── 所有 anchor EC 自身高度为 0 ── */
[data-testid="stElementContainer"]:has(#wp-import-container-anchor),
[data-testid="stElementContainer"]:has(#wp-import-body-anchor),
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor),
[data-testid="stElementContainer"]:has(#wp-import-dl-anchor),
[data-testid="stElementContainer"]:has(#wp-liab-import-container-anchor),
[data-testid="stElementContainer"]:has(#wp-liab-import-body-anchor),
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor),
[data-testid="stElementContainer"]:has(#wp-liab-import-dl-anchor) {
  height: 0 !important;
  overflow: visible !important;
  margin: 0 !important;
  padding: 0 !important;
}

/* ── 全局字体：对齐表格/Panel 字体系统 ── */
[data-testid="stElementContainer"]:has(#wp-import-card-anchor) ~ [data-testid="stLayoutWrapper"] *,
[data-testid="stElementContainer"]:has(#wp-liab-import-card-anchor) ~ [data-testid="stLayoutWrapper"] * {
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
  line-height: 1.5 !important;
}

/* ══════════════════════════════════════════════════════
   Tabs：对齐资产表格表头风格
   position: relative 为下载按钮绝对定位提供锚点
══════════════════════════════════════════════════════ */
/* Tabs 选择器：tabsAnchor EC 后紧邻的是 stTabs（直接兄弟，不经过 stElementContainer） */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"],
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] {
  position: relative !important;
}
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] [data-baseweb="tab-list"],
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] [data-baseweb="tab-list"] {
  border-bottom: 1px solid #F3F4F6 !important;
  gap: 20px !important;
  background: transparent !important;
  padding-bottom: 0 !important;
  min-height: 32px !important;
  padding-right: 110px !important;
}
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] button[data-baseweb="tab"],
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] button[data-baseweb="tab"] {
  background: transparent !important;
  border: none !important;
  border-bottom: 2px solid transparent !important;
  margin-bottom: -1px !important;
  border-radius: 0 !important;
  color: #9CA3AF !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  padding: 5px 0 !important;
  transition: color .12s ease !important;
}
/* 强制覆盖 Tab 内部 p 标签的字号 */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] button[data-baseweb="tab"] p,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] button[data-baseweb="tab"] p {
  font-size: 13px !important;
  font-weight: 500 !important;
}
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] button[data-baseweb="tab"]:hover,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] button[data-baseweb="tab"]:hover {
  color: #1E3A5F !important;
}
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] button[aria-selected="true"][data-baseweb="tab"],
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] button[aria-selected="true"][data-baseweb="tab"] {
  color: #1B2A4A !important;
  font-weight: 600 !important;
  border-bottom: 2px solid transparent !important;
}
/* Streamlit 默认红色 tab-highlight 覆盖为品牌深蓝 */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] [data-baseweb="tab-highlight"],
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] [data-baseweb="tab-highlight"] {
  background-color: #1B2A4A !important;
  background: #1B2A4A !important;
}
/* Tab 内容区：压缩上方 padding */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stTabsContent"],
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stTabsContent"] {
  padding-top: 8px !important;
}

/* ══════════════════════════════════════════════════════
   下载按钮：绝对定位到内容区右上角（Tabs 行旁边）
   containing block = stLayoutWrapper（已设 position: relative）
══════════════════════════════════════════════════════ */
[data-testid="stElementContainer"]:has(#wp-import-dl-anchor) + [data-testid="stElementContainer"],
[data-testid="stElementContainer"]:has(#wp-liab-import-dl-anchor) + [data-testid="stElementContainer"] {
  position: absolute !important;
  top: 10px !important;
  right: 16px !important;
  width: auto !important;
  z-index: 10 !important;
  margin: 0 !important;
  padding: 0 !important;
}
[data-testid="stElementContainer"]:has(#wp-import-dl-anchor) + [data-testid="stElementContainer"] .stDownloadButton > button,
[data-testid="stElementContainer"]:has(#wp-liab-import-dl-anchor) + [data-testid="stElementContainer"] .stDownloadButton > button {
  background: #ffffff !important;
  border: 1px solid #E5E7EB !important;
  border-radius: 6px !important;
  color: #374151 !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  padding: 3px 10px !important;
  box-shadow: none !important;
  white-space: nowrap !important;
  transition: all .15s ease !important;
  height: auto !important;
  min-height: 0 !important;
  line-height: 1.4 !important;
}
/* 强制覆盖下载按钮内部 p 标签的字号 */
[data-testid="stElementContainer"]:has(#wp-import-dl-anchor) + [data-testid="stElementContainer"] .stDownloadButton > button p,
[data-testid="stElementContainer"]:has(#wp-liab-import-dl-anchor) + [data-testid="stElementContainer"] .stDownloadButton > button p {
  font-size: 13px !important;
  font-weight: 500 !important;
}
[data-testid="stElementContainer"]:has(#wp-import-dl-anchor) + [data-testid="stElementContainer"] .stDownloadButton > button:hover,
[data-testid="stElementContainer"]:has(#wp-liab-import-dl-anchor) + [data-testid="stElementContainer"] .stDownloadButton > button:hover {
  border-color: #D1D5DB !important;
  background: #F9FAFB !important;
}

/* ══════════════════════════════════════════════════════
   Uploader 轻量化：单行入口，去掉大灰块
   注意：Streamlit 实际 testid 是 stFileUploaderDropzone（不是 stFileUploadDropzone）
══════════════════════════════════════════════════════ */
/* 整体容器：去掉背景、边框、padding，不再是独立卡片块 */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ * [data-testid="stFileUploader"],
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ * [data-testid="stFileUploader"] {
  background: transparent !important;
  border: none !important;
  padding: 0 !important;
  margin-top: 6px !important;
  box-shadow: none !important;
}
/* label 标签：隐藏（说明文字已用 st.caption 替代） */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ * [data-testid="stWidgetLabel"],
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ * [data-testid="stWidgetLabel"] {
  display: none !important;
}
/* Dropzone section：压缩为单行高度，透明背景，极细边框 */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzone"],
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzone"] {
  background: transparent !important;
  border: 1px solid #E5E7EB !important;
  border-radius: 6px !important;
  padding: 4px 10px !important;
  min-height: 0 !important;
  height: auto !important;
  display: flex !important;
  flex-direction: row !important;
  align-items: center !important;
  gap: 8px !important;
}
/* Dropzone 内部说明区：flex 横排 */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzoneInstructions"],
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzoneInstructions"] {
  display: flex !important;
  align-items: center !important;
  gap: 6px !important;
  flex-direction: row !important;
  flex-wrap: nowrap !important;
  flex: 1 !important;
  padding: 0 !important;
  min-height: 0 !important;
}
/* Dropzone 内部说明区的 span（云朵图标容器）：隐藏 */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzoneInstructions"] > span:first-child,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzoneInstructions"] > span:first-child {
  display: none !important;
}
/* Dropzone 内部说明区的 div（文字容器）：横排 */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzoneInstructions"] > div,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzoneInstructions"] > div {
  display: flex !important;
  flex-direction: row !important;
  align-items: center !important;
  gap: 6px !important;
  flex-wrap: nowrap !important;
}
/* 隐藏云朵 SVG 图标 */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzoneInstructions"] svg,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzoneInstructions"] svg {
  display: none !important;
}
/* "Drag and drop" 提示文字：单行，极小字号，灰色 */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzoneInstructions"] span,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzoneInstructions"] span {
  font-size: 11px !important;
  color: #9CA3AF !important;
  white-space: nowrap !important;
  line-height: 1.4 !important;
}
/* "Browse files" 按钮：轻量链接风格，精确选择器 */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzone"] button,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzone"] button {
  background: #F9FAFB !important;
  border: 1px solid #E5E7EB !important;
  border-radius: 4px !important;
  color: #374151 !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  padding: 2px 10px !important;
  box-shadow: none !important;
  white-space: nowrap !important;
  flex-shrink: 0 !important;
  height: 24px !important;
  min-height: 0 !important;
  line-height: 1 !important;
}
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzone"] button:hover,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ * [data-testid="stFileUploaderDropzone"] button:hover {
  background: #F3F4F6 !important;
  border-color: #D1D5DB !important;
}

/* ══════════════════════════════════════════════════════
   Tab 内部其他控件（选择器基于 stTabs，精确作用于 Tab 内容区）
══════════════════════════════════════════════════════ */
/* 说明文字（st.caption）：紧凑，对齐表格辅助文字 */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stCaptionContainer"] p,
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stMarkdown"] p,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stCaptionContainer"] p,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stMarkdown"] p {
  color: #6B7280 !important;
  font-size: 12px !important;
  font-weight: 400 !important;
  line-height: 1.5 !important;
  margin-bottom: 4px !important;
  margin-top: 0 !important;
}

/* Radio 按钮组：pill 样式 */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] .stRadio > div,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] .stRadio > div {
  gap: 6px !important;
  flex-direction: row !important;
  flex-wrap: wrap !important;
  margin-bottom: 6px !important;
}
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] .stRadio label,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] .stRadio label {
  border: 1px solid #E5E7EB !important;
  border-radius: 6px !important;
  padding: 3px 10px !important;
  font-size: 11px !important;
  font-weight: 400 !important;
  color: #6B7280 !important;
  background: #fff !important;
  cursor: pointer !important;
  transition: all .12s !important;
  margin: 0 !important;
}
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] .stRadio label:has(input:checked),
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] .stRadio label:has(input:checked) {
  background: #EFF6FF !important;
  border-color: #1E3A5F !important;
  color: #1E3A5F !important;
  font-weight: 600 !important;
}
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] .stRadio input,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] .stRadio input {
  display: none !important;
}
/* ── Typography: radio label > p 字号修复（解决 Streamlit 16px 继承）── */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] .stRadio label p,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] .stRadio label p {
  font-size: 11px !important;
  font-weight: inherit !important;
  color: inherit !important;
  line-height: 1.4 !important;
  margin: 0 !important;
}

/* 主操作按钮（确认导入）：Fintech 蓝，紧凑比例 */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] .stButton > button[kind="primary"],
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] .stButton > button[kind="primary"] {
  background: #1E3A5F !important;
  border: none !important;
  border-radius: 6px !important;
  color: #ffffff !important;
  font-size: 12px !important;
  font-weight: 600 !important;
  padding: 4px 14px !important;
  margin-top: 8px !important;
}

/* ── Typography: st.info 提示条 — 去掉蓝底，改为内联说明文字 ── */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stAlert"],
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stAlert"] {
  background: transparent !important;
  border: none !important;
  border-radius: 0 !important;
  padding: 0 0 4px 0 !important;
  margin-bottom: 4px !important;
  box-shadow: none !important;
}
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stAlert"] p,
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stAlert"] [data-testid="stMarkdownContainer"] p,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stAlert"] p,
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stAlert"] [data-testid="stMarkdownContainer"] p {
  font-size: 12px !important;
  font-weight: 400 !important;
  color: #6B7280 !important;
  line-height: 1.5 !important;
}
/* 隐藏 alert 图标（info icon） */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stAlert"] [data-testid="stAlertContentIcon"],
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stAlert"] [data-testid="stAlertContentIcon"] {
  display: none !important;
}

/* DataFrame 预览：极细边框 */
[data-testid="stElementContainer"]:has(#wp-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stDataFrame"],
[data-testid="stElementContainer"]:has(#wp-liab-import-tabs-anchor) ~ [data-testid="stTabs"] [data-testid="stDataFrame"] {
  border: 1px solid #F3F4F6 !important;
  border-radius: 6px !important;
  overflow: hidden !important;
  margin-top: 6px !important;
    }
    </style>""", unsafe_allow_html=True)


    # ── 构建顶部 HTML（标题 + KPI + 图表 + 资产明细）────────────────────────
    html_top = _build_overview_html(bs, positions, alerts, portfolio, liabilities)
    height_top = (
        640   # header + KPI + chart row
        + 480  # 资产明细
        - 56  # 补偿：content-left底部无padding(-40) + components.html自动+16px(-16)
    )
    components.html(html_top, height=height_top, scrolling=False)

    # ── 导入 / 导出区（资产明细与负债明细之间）──────────────────────────────
    # v1.10.4: 用 @st.fragment 包裹，点击展开/收起只 rerun fragment，不触发整页 rerun
    # 这样滚动位置不会跳动，体验更顺
    _render_import_card(portfolio_id)

    # ── 构建底部 HTML（仅负债明细）──────────────────────────────────────────
    # 拆分 html_bottom，将导入模块插入负债明细与风险告警之间
    html_liab_table = _build_liab_table_html(bs, liabilities)
    # 调整高度计算，确保 HTML 内部 Padding 能够正确显示
    height_liab_table = 80 + n_inv_liab * 42 + 40
    components.html(html_liab_table, height=height_liab_table, scrolling=False)

    # ── 负债导入 / 导出区（负债明细下方，风险告警上方）──────────────────────
    _render_liab_import_card(portfolio_id)

    # ── 风险告警（有告警才渲染 iframe）──────────────────────────────────────
    if n_alerts > 0:
        html_alerts = _build_alerts_ai_html(bs, alerts)
        height_alerts = n_alerts * 95 + 70
        components.html(html_alerts, height=height_alerts, scrolling=False)

    # ── AI 综合分析报告（Streamlit 原生 fragment）────────────────────────────
    _render_ai_report_section(portfolio_id)


# ══════════════════════════════════════════════════════════════════════════════
# AI 综合分析报告（fragment）
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment
def _render_ai_report_section(portfolio_id: int):
    """AI 综合分析报告区（fragment，按钮点击只 rerun 本区域）"""
    if "overview_ai_report" not in st.session_state:
        st.session_state["overview_ai_report"] = None

    # 卡片头部样式
    st.markdown("""
<div style="background:linear-gradient(135deg,#EFF6FF 0%,#F0FDF4 100%);
            border:1px solid #BFDBFE;border-radius:12px;
            padding:18px 24px 14px;margin-top:4px;">
  <div style="font-size:14px;font-weight:700;color:#1B2A4A;margin-bottom:5px;">✨ AI 综合分析报告</div>
  <div style="font-size:12px;color:#6B7280;line-height:1.6;">
    报告将融合：账户总览 · 投资纪律检查 · 偏离度分析 · 风险告警，生成个性化投资建议。
  </div>
</div>""", unsafe_allow_html=True)

    st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)

    if st.button("✨ 生成报告", key="overview_ai_generate_btn", type="primary"):
        with st.spinner("AI 分析中，请稍候…"):
            try:
                from decision_engine import data_loader, llm_engine
                _QUERY = "请综合分析我当前的投资组合，从持仓集中度、资产配置、风险敞口三个维度给出评估，并给出具体的调仓建议。"
                loaded = data_loader.load(asset_name=None, pid=portfolio_id)
                result = llm_engine.review_portfolio(_QUERY, loaded)
                st.session_state["overview_ai_report"] = result.chat_answer or "（AI 未返回内容，请重试）"
            except Exception as e:
                st.session_state["overview_ai_report"] = f"❌ 分析失败：{e}"

    report = st.session_state.get("overview_ai_report")
    if report:
        st.markdown(
            f'<div style="background:#fff;border:1px solid #E5E7EB;border-radius:10px;'
            f'padding:16px 20px;font-size:13px;line-height:1.8;color:#374151;margin-top:10px;">'
            f'{report}'
            f'<div style="margin-top:10px;font-size:11px;color:#9CA3AF;">'
            f'⚖️ 仅供参考，不构成投资建议。投资有风险，入市需谨慎。</div></div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# 导入 / 导出卡片（fragment）+ 导入 / 导出面板（Streamlit 原生）
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment
def _render_import_card(portfolio_id: int):
    """导入/导出卡片外壳（fragment）
    用 @st.fragment 装饰，点击展开/收起只 rerun 这个 fragment，不触发整页 rerun
    这样滚动位置不会跳动
    """
    # 初始化 session_state
    if "import_panel_open" not in st.session_state:
        st.session_state["import_panel_open"] = False
    is_open = st.session_state["import_panel_open"]

    # 卡片头部：用 st.button 实现整个头部区域可点击，用 CSS 将其渲染成卡片头部外观
    toggle_text = "📥  导入 / 导出数据        ▲" if is_open else "📥  导入 / 导出数据        ▼"
    st.markdown('<div id="wp-import-card-anchor"></div>', unsafe_allow_html=True)
    if st.button(toggle_text, key="wp_import_toggle", use_container_width=True):
        st.session_state["import_panel_open"] = not is_open
        st.rerun()

    # 内容区：仅展开时渲染
    # 用 st.container 创建独立的 stVerticalBlock，使 ~ 选择器只作用于内容区内部，不影响底部 iframe
    if is_open:
        # 内联 margin-top:-16px 消除 stVerticalBlock gap，不依赖 CSS 选择器
        st.markdown('<div id="wp-import-body-anchor" style="margin-top:-16px"></div>', unsafe_allow_html=True)
        # border=False：外层卡片边框已由 CSS stLayoutWrapper 选择器统一控制
        # 不需要 border=True 再叠一层白底圆角框（消除双层卡片感）
        with st.container(border=False):
            st.markdown('<div id="wp-import-container-anchor"></div>', unsafe_allow_html=True)
            _render_import_panel(portfolio_id)


def _render_import_panel(pid: int):
    """导入导出功能面板（CSV / 截图识别）"""
    session = get_session()
    try:
        positions_all = session.query(Position).filter_by(
            portfolio_id=pid, segment="投资"
        ).all()
    finally:
        session.close()

    # ── Tabs 先渲染（占据左侧），下载按钮后渲染，CSS 绝对定位到 Tabs 行右上角 ──
    # 用 wp-import-tabs-anchor 标记 Tabs 容器，使 position:relative 作用域正确
    st.markdown('<div id="wp-import-tabs-anchor"></div>', unsafe_allow_html=True)
    tab_generic, tab_broker, tab_bank = st.tabs([
        "通用 CSV（全量覆盖）", "CSV 导入（按平台替换）", "截图识别（按平台替换）",
    ])

    with tab_generic:
        st.caption("上传后将全量覆盖全部投资持仓，养老/公积金数据不受影响。")
        uploaded = st.file_uploader("选择持仓 CSV 文件", type=["csv"], key="pos_upload")
        if uploaded:
            content = uploaded.read().decode("utf-8-sig")
            new_positions, errors = parse_positions_csv(content)
            if errors:
                for e in errors: st.error(e)
            elif new_positions:
                st.success(f"解析成功，共 {len(new_positions)} 条持仓。")
                if st.button("确认覆盖全部资产数据", key="confirm_pos_import"):
                    _import_positions_by_segment(pid, new_positions, "投资")
                    st.cache_data.clear(); st.rerun(scope="app")

    with tab_broker:
        st.caption("直接导入老虎证券对账单或富途持仓 CSV，只替换该平台数据，其他平台不受影响。")
        broker = st.radio("选择券商", ["老虎证券", "富途证券"], horizontal=True, key="broker_select")
        broker_file = st.file_uploader(f"上传 {broker} CSV", type=["csv"], key=f"broker_upload_{broker}")
        if broker_file:
            from app.platform_importers import parse_tiger_csv, parse_futu_csv
            content = broker_file.read().decode("utf-8-sig")
            positions_parsed, rate = parse_tiger_csv(content) if broker == "老虎证券" else parse_futu_csv(content)
            if positions_parsed:
                preview_rows = [{
                    "资产名称": p["name"], "代码": p["ticker"], "大类": p["asset_class"],
                    "市值(USD)": f"${p['original_value']:,.2f}",
                    "市值(CNY)": f"¥{p['market_value_cny']:,}",
                    "盈亏(USD)": f"{'+' if p['profit_loss_original_value']>=0 else ''}${p['profit_loss_original_value']:,.2f}",
                } for p in positions_parsed]
                st.success(f"解析成功，共 {len(positions_parsed)} 条持仓，汇率 USD/CNY = {rate:.4f}")
                st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)
                if st.button(f"确认导入 {broker} 数据", key="confirm_broker_import", type="primary"):
                    _import_positions_by_platform(pid, positions_parsed, broker)
                    st.cache_data.clear(); st.rerun(scope="app")
            else:
                st.error("未能解析到持仓数据，请检查文件格式。")

    with tab_bank:
        st.caption("上传 APP 截图，AI 自动识别持仓数据，只替换该平台数据。")
        _BANK_LIST   = ["招商银行", "支付宝", "建设银行"]
        _BROKER_LIST = ["国金证券", "雪盈证券"]
        platform = st.radio("选择平台", _BANK_LIST + _BROKER_LIST, horizontal=True, key="bank_select")
        _hint = {
            "招商银行": "将识别：活钱管理、稳健投资、进取投资",
            "支付宝":   "将识别：活钱管理、稳健投资、进取投资",
            "建设银行": "将识别：活钱、理财产品、债券、基金",
            "国金证券": "将识别：所有港股持仓（名称、头寸、市值人民币、盈亏人民币、盈亏%）",
            "雪盈证券": "将识别：所有美股持仓（名称、代码、头寸、市值美元、盈亏美元、盈亏%）",
        }
        st.info(_hint[platform])
        upload_counter = st.session_state.get("bank_upload_counter", 0)
        img_file = st.file_uploader("上传截图（JPG/PNG）", type=["jpg","jpeg","png"],
                                    key=f"bank_img_{platform}_{upload_counter}")
        if img_file:
            img_bytes = img_file.read()
            st.image(img_bytes, caption="已上传截图", width=300)
            cache_key = f"bank_result_{platform}_{len(img_bytes)}"
            if platform in _BANK_LIST:
                from app.bank_screenshot import parse_bank_screenshot, bank_positions_to_db
                if cache_key not in st.session_state:
                    with st.spinner("AI 识别中..."):
                        result, error = parse_bank_screenshot(img_bytes, platform)
                    st.session_state[cache_key] = (result, error)
                else:
                    result, error = st.session_state[cache_key]
                if error:
                    st.error(f"识别失败：{error}")
                    if cache_key in st.session_state: del st.session_state[cache_key]
                else:
                    st.success("识别成功，请确认以下数据：")
                    st.dataframe(pd.DataFrame([{"分类": k, "识别金额(元)": f"{v:,.2f}"} for k, v in result.items()]),
                                 use_container_width=True, hide_index=True)
                    if st.button(f"确认导入 {platform} 数据", key="confirm_bank_import", type="primary"):
                        positions_to_update = bank_positions_to_db(result, platform)
                        updated_count = _update_bank_positions(pid, positions_to_update, platform)
                        if updated_count > 0:
                            del st.session_state[cache_key]
                            st.session_state["bank_upload_counter"] = upload_counter + 1
                            st.cache_data.clear(); st.rerun(scope="app")
                        else:
                            st.error("⚠️ 未找到匹配的持仓记录，数据未更新。")
            else:
                from app.bank_screenshot import parse_broker_screenshot, broker_positions_to_db
                if cache_key not in st.session_state:
                    with st.spinner("AI 识别持仓中..."):
                        broker_positions, error = parse_broker_screenshot(img_bytes, platform)
                    st.session_state[cache_key] = (broker_positions, error)
                else:
                    broker_positions, error = st.session_state[cache_key]
                if error:
                    st.error(f"识别失败：{error}")
                    if cache_key in st.session_state: del st.session_state[cache_key]
                else:
                    st.success(f"识别成功，共 {len(broker_positions)} 条持仓，请确认：")
                    if platform == "雪盈证券":
                        preview_rows = [{"名称": p.get("name",""), "代码": p.get("ticker",""),
                            "头寸": int(p.get("quantity",0)),
                            "市值(美元)": f"{p.get('market_value_usd',0):,.2f}",
                            "盈亏(美元)": f"{'+' if p.get('pnl_usd',0)>=0 else ''}{p.get('pnl_usd',0):,.2f}",
                            "盈亏%": f"{'+' if p.get('pnl_pct',0)>=0 else ''}{p.get('pnl_pct',0):.2f}%",
                        } for p in broker_positions]
                    else:
                        preview_rows = [{"名称": p.get("name",""), "代码": p.get("ticker",""),
                            "头寸": int(p.get("quantity",0)),
                            "市值(人民币)": f"{p.get('market_value_cny',0):,.2f}",
                            "盈亏(人民币)": f"{'+' if p.get('pnl_cny',0)>=0 else ''}{p.get('pnl_cny',0):,.2f}",
                            "盈亏%": f"{'+' if p.get('pnl_pct',0)>=0 else ''}{p.get('pnl_pct',0):.2f}%",
                        } for p in broker_positions]
                    st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)
                    if st.button(f"确认导入 {platform} 数据", key="confirm_bank_import", type="primary"):
                        db_positions = broker_positions_to_db(broker_positions, platform)
                        _import_positions_by_platform(pid, db_positions, platform)
                        del st.session_state[cache_key]
                        st.session_state["bank_upload_counter"] = upload_counter + 1
                        st.cache_data.clear(); st.rerun(scope="app")

    # ── 下载按钮：渲染在 Tabs 之后，CSS 绝对定位到 Tabs 行右上角 ──
    st.markdown('<div id="wp-import-dl-anchor"></div>', unsafe_allow_html=True)
    csv_str = positions_to_csv(session_positions_reload(portfolio_id, segment="投资"))
    st.download_button(
        "⬇ 下载 CSV",
        data=csv_str.encode("utf-8-sig"),
        file_name="positions.csv", mime="text/csv",
        key="wp_dl_csv",
    )

@st.fragment
def _render_liab_import_card(portfolio_id: int):
    """负债导入/导出卡片外壳（fragment）
    用 @st.fragment 装饰，点击展开/收起只 rerun 这个 fragment，不触发整页 rerun
    这样滚动位置不会跳动
    """
    # 初始化 session_state
    if "liab_import_panel_open" not in st.session_state:
        st.session_state["liab_import_panel_open"] = False
    is_open = st.session_state["liab_import_panel_open"]

    # 卡片头部：用 st.button 实现整个头部区域可点击，用 CSS 将其渲染成卡片头部外观
    toggle_text = "📥  导入 / 导出数据        ▲" if is_open else "📥  导入 / 导出数据        ▼"
    st.markdown('<div id="wp-liab-import-card-anchor"></div>', unsafe_allow_html=True)
    if st.button(toggle_text, key="wp_liab_import_toggle", use_container_width=True):
        st.session_state["liab_import_panel_open"] = not is_open
        st.rerun()

    # 内容区：仅展开时渲染
    # 用 st.container 创建独立的 stVerticalBlock，使 ~ 选择器只作用于内容区内部，不影响底部 iframe
    if is_open:
        # 内联 margin-top:-16px 消除 stVerticalBlock gap，不依赖 CSS 选择器
        st.markdown('<div id="wp-liab-import-body-anchor" style="margin-top:-16px"></div>', unsafe_allow_html=True)
        # border=False：外层卡片边框已由 CSS stLayoutWrapper 选择器统一控制
        # 不需要 border=True 再叠一层白底圆角框（消除双层卡片感）
        with st.container(border=False):
            st.markdown('<div id="wp-liab-import-container-anchor"></div>', unsafe_allow_html=True)
            _render_liab_import_panel(portfolio_id)


def _render_liab_import_panel(pid: int):
    """负债导入导出功能面板（仅保留通用 CSV 选项）"""
    session = get_session()
    try:
        liabilities_all = session.query(Liability).filter_by(
            portfolio_id=pid
        ).all()
    finally:
        session.close()

    # ── Tabs 先渲染（占据左侧），下载按钮后渲染，CSS 绝对定位到 Tabs 行右上角 ──
    # 用 wp-liab-import-tabs-anchor 标记 Tabs 容器，使 position:relative 作用域正确
    st.markdown('<div id="wp-liab-import-tabs-anchor"></div>', unsafe_allow_html=True)
    tab_generic = st.tabs(["通用 CSV（全量覆盖）"])[0]

    with tab_generic:
        st.caption("上传后将全量覆盖全部负债数据。")
        uploaded = st.file_uploader("选择负债 CSV 文件", type=["csv"], key="liab_upload")
        if uploaded:
            content = uploaded.read().decode("utf-8-sig")
            new_liabilities, errors = parse_liabilities_csv(content)
            if errors:
                for e in errors: st.error(e)
            elif new_liabilities:
                st.success(f"解析成功，共 {len(new_liabilities)} 条负债。")
                if st.button("确认覆盖全部负债数据", key="confirm_liab_import"):
                    _import_liabilities_by_purpose(pid, new_liabilities, ["投资杠杆", "购房", "日常消费"])
                    st.cache_data.clear(); st.rerun(scope="app")

    # ── 下载按钮：渲染在 Tabs 之后，CSS 绝对定位到 Tabs 行右上角 ──
    st.markdown('<div id="wp-liab-import-dl-anchor"></div>', unsafe_allow_html=True)
    session = get_session()
    try:
        liabilities_reload = session.query(Liability).filter_by(portfolio_id=pid).all()
    finally:
        session.close()
    csv_str = liabilities_to_csv(liabilities_reload)
    st.download_button(
        "⬇ 下载 CSV",
        data=csv_str.encode("utf-8-sig"),
        file_name="liabilities.csv", mime="text/csv",
        key="wp_liab_dl_csv",
    )


# ══════════════════════════════════════════════════════════════════════════════
# 工具函数（逻辑层，保持不变）
# ══════════════════════════════════════════════════════════════════════════════

def session_positions_reload(pid: int, segment: str = None):
    session = get_session()
    try:
        q = session.query(Position).filter_by(portfolio_id=pid)
        if segment:
            q = q.filter_by(segment=segment)
        return q.all()
    finally:
        session.close()


def _import_positions_by_segment(pid: int, new_positions: list, segment: str):
    session = get_session()
    try:
        session.query(Position).filter_by(portfolio_id=pid, segment=segment).delete()
        for p in new_positions:
            p_data = dict(p); p_data["segment"] = segment
            session.add(Position(portfolio_id=pid, **p_data))
        session.commit()
    except Exception as e:
        session.rollback(); st.error(f"导入失败: {e}")
    finally:
        session.close()


def _import_positions_by_platform(pid: int, positions: list, platform: str):
    session = get_session()
    try:
        session.query(Position).filter_by(
            portfolio_id=pid, platform=platform, segment="投资"
        ).delete()
        for p_data in positions:
            session.add(Position(portfolio_id=pid, **p_data))
        session.commit()
    except Exception as e:
        session.rollback(); st.error(f"导入失败: {e}")
    finally:
        session.close()


def _import_liabilities_by_purpose(pid: int, new_liabilities: list, purposes: list):
    session = get_session()
    try:
        for purpose in purposes:
            session.query(Liability).filter_by(portfolio_id=pid, purpose=purpose).delete()
        for l in new_liabilities:
            if l.get("purpose") in purposes:
                session.add(Liability(portfolio_id=pid, **l))
        session.commit()
    except Exception as e:
        session.rollback(); st.error(f"导入失败: {e}")
    finally:
        session.close()


def _update_bank_positions(pid: int, updates: list, platform: str) -> int:
    session = get_session()
    count = 0
    try:
        for item in updates:
            p = session.query(Position).filter_by(
                portfolio_id=pid, platform=platform,
                name=item["name"], segment="投资",
            ).first()
            if p:
                p.market_value_cny = item["market_value_cny"]
                p.original_value   = item["market_value_cny"]
                count += 1
        session.commit()
    except Exception as e:
        session.rollback(); st.error(f"更新失败: {e}")
    finally:
        session.close()
    return count
