"""
WealthPilot UI v1.0 — 可复用组件库
====================================
基于已定版的 UI 设计系统封装的 Streamlit 组件。
所有组件均通过 st.markdown() 注入 HTML/CSS，保持与 v1.0 设计系统一致。

使用方式：
    from ui_components import inject_global_css, kpi_primary, kpi_secondary, ...
    inject_global_css()  # 必须在页面最开始调用一次
"""

import streamlit as st


# ═══════════════════════════════════════════════════════════════════
# 0. 全局 CSS 注入（每个页面调用一次）
# ═══════════════════════════════════════════════════════════════════

def inject_global_css():
    """
    注入 WealthPilot v1.0 全局 CSS 设计系统。
    必须在每个页面的最开始调用一次。
    """
    st.markdown("""
<style>
/* ── Design Tokens ─────────────────────────────── */
:root {
  --ocean-900: #0F1E35;
  --ocean-800: #1B2A4A;
  --ocean-600: #2D4A7A;
  --ocean-50:  #F4F6FA;
  --blue-500:  #3B82F6;
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
  --shadow-sm: 0 1px 3px rgba(15,30,53,0.07), 0 1px 2px rgba(15,30,53,0.04);
  --shadow-dark: 0 6px 20px rgba(15,30,53,0.28);
  --radius: 12px;
  /* ── Typography System Tokens ───────────────────── */
  --wp-text-h1:     20px;
  --wp-text-h2:     14px;
  --wp-text-title:  13px;
  --wp-text-nav:    12px;
  --wp-text-body:   13px;
  --wp-text-desc:   12px;
  --wp-text-meta:   11px;
  --wp-text-label:  11px;
  --wp-color-h1:    #1B2A4A;
  --wp-color-title: #374151;
  --wp-color-nav:   #6B7280;
  --wp-color-body:  #374151;
  --wp-color-desc:  #6B7280;
  --wp-color-meta:  #9CA3AF;
  --wp-color-label: #9CA3AF;
}

/* ── 全局重置 ─────────────────────────────────── */
.stApp { background: var(--ocean-50) !important; }
.block-container { padding: 24px !important; max-width: 100% !important; }
.stMarkdown p { margin: 0; }

/* ── 侧边栏 ───────────────────────────────────── */
section[data-testid="stSidebar"] > div:first-child {
  background: linear-gradient(180deg, var(--ocean-800) 0%, var(--ocean-900) 100%) !important;
  border-right: 1px solid rgba(255,255,255,0.05);
}
section[data-testid="stSidebar"] .stButton > button {
  background: transparent;
  border: none;
  color: rgba(255,255,255,0.48);
  font-size: 12px;
  font-weight: 400;
  text-align: left;
  padding: 6px 8px 6px 28px;
  border-radius: 7px;
  width: 100%;
  transition: all 0.14s;
}
section[data-testid="stSidebar"] .stButton > button:hover {
  background: rgba(255,255,255,0.06);
  color: rgba(255,255,255,0.78);
}

/* ── 通用卡片 ─────────────────────────────────── */
.wp-card {
  background: #fff;
  border: 1px solid var(--gray-200);
  border-radius: var(--radius);
  padding: 20px;
  box-shadow: var(--shadow-sm);
  margin-bottom: 16px;
}
.wp-card-title {
  font-size: 13px; font-weight: 600; color: var(--gray-700);
  display: flex; align-items: center; gap: 6px;
  margin-bottom: 16px;
}
.wp-card-badge {
  margin-left: auto; font-size: 11px; font-weight: 400; color: var(--gray-400);
}

/* ── KPI 主卡 ─────────────────────────────────── */
.wp-kpi-primary {
  background: linear-gradient(135deg, var(--ocean-800) 0%, var(--ocean-900) 100%);
  border-radius: var(--radius);
  padding: 20px 24px;
  box-shadow: var(--shadow-dark);
  margin-bottom: 0;
}
.wp-kpi-primary .label {
  font-size: 11px; font-weight: 600; color: rgba(255,255,255,0.48);
  text-transform: uppercase; letter-spacing: 0.6px;
}
.wp-kpi-primary .value {
  font-size: 28px; font-weight: 700; color: #fff;
  letter-spacing: -1px; font-variant-numeric: tabular-nums;
  margin: 4px 0 8px; line-height: 1.1;
}
.wp-kpi-primary .sub {
  display: flex; gap: 20px;
  font-size: 13px; color: rgba(255,255,255,0.55);
}
.wp-kpi-primary .sub strong { color: rgba(255,255,255,0.9); font-weight: 600; }

/* ── KPI 次卡 ─────────────────────────────────── */
.wp-kpi-secondary {
  background: #fff;
  border: 1px solid var(--gray-200);
  border-radius: var(--radius);
  padding: 16px 18px;
  box-shadow: var(--shadow-sm);
  margin-bottom: 0;
  height: 100%;
}
.wp-kpi-secondary .label {
  font-size: 11px; font-weight: 600; color: var(--gray-400);
  text-transform: uppercase; letter-spacing: 0.5px;
}
.wp-kpi-secondary .value {
  font-size: 20px; font-weight: 700; color: var(--ocean-800);
  letter-spacing: -0.5px; font-variant-numeric: tabular-nums;
  margin-top: 4px;
}
.wp-kpi-secondary .value.pos { color: var(--green-600); }
.wp-kpi-secondary .value.neg { color: var(--red-600); }
.wp-kpi-secondary .delta {
  font-size: 11px; font-weight: 600; margin-top: 4px;
}
.wp-kpi-secondary .delta.pos  { color: var(--green-600); }
.wp-kpi-secondary .delta.neg  { color: var(--red-600); }
.wp-kpi-secondary .delta.warn { color: var(--amber-500); }

/* ── KPI 辅助卡 ───────────────────────────────── */
.wp-kpi-tertiary {
  background: #fff;
  border: 1px solid var(--gray-200);
  border-radius: var(--radius);
  padding: 16px 18px;
  box-shadow: var(--shadow-sm);
  margin-bottom: 0;
  height: 100%;
}
.wp-kpi-tertiary .label {
  font-size: 11px; font-weight: 500; color: var(--gray-400);
  text-transform: uppercase; letter-spacing: 0.5px;
}
.wp-kpi-tertiary .value {
  font-size: 18px; font-weight: 600; color: var(--gray-700);
  letter-spacing: -0.3px; font-variant-numeric: tabular-nums;
  margin-top: 4px;
}
.wp-kpi-tertiary .value.alert { color: var(--red-600); }
.wp-kpi-tertiary .delta {
  font-size: 11px; font-weight: 600; margin-top: 4px;
}
.wp-kpi-tertiary .delta.neg { color: var(--red-600); }

/* ── 偏差视图 ─────────────────────────────────── */
.wp-deviation-item {
  display: grid;
  grid-template-columns: 52px 1fr 64px 88px;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--gray-100);
}
.wp-deviation-item:last-child { border-bottom: none; }
.wp-dev-name { font-size: 13px; font-weight: 500; color: var(--gray-700); }
.wp-dev-bar-wrap {
  position: relative; height: 8px;
  background: var(--gray-100); border-radius: 4px;
}
.wp-dev-bar-range {
  position: absolute; top: 0; bottom: 0;
  background: rgba(59,130,246,0.12); border-radius: 4px;
}
.wp-dev-bar-dot {
  position: absolute; top: 50%;
  transform: translate(-50%, -50%);
  width: 12px; height: 12px; border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0,0,0,0.12); z-index: 2;
}
.wp-dev-bar-mid {
  position: absolute; top: -2px; bottom: -2px;
  width: 2px; background: rgba(59,130,246,0.35); border-radius: 1px;
}
.wp-dev-current {
  font-size: 13px; font-weight: 600; color: var(--ocean-800);
  font-variant-numeric: tabular-nums; text-align: right;
}
.wp-dev-badge {
  display: inline-flex; align-items: center; gap: 3px;
  padding: 2px 8px; border-radius: 5px;
  font-size: 11px; font-weight: 600; justify-content: center;
}
.wp-dev-badge.over  { background: var(--red-100);   color: var(--red-600); }
.wp-dev-badge.under { background: var(--blue-100);  color: #1D4ED8; }
.wp-dev-badge.ok    { background: var(--green-100); color: var(--green-600); }

/* ── 金融表格 ─────────────────────────────────── */
.wp-table {
  width: 100%; border-collapse: collapse; font-size: 13px;
}
.wp-table thead th {
  padding: 8px 10px;
  font-size: 11px; font-weight: 600; color: var(--gray-400);
  text-transform: uppercase; letter-spacing: 0.4px;
  border-bottom: 1px solid var(--gray-200);
  white-space: nowrap; background: #fff;
}
.wp-table thead th.r { text-align: right; }
.wp-table tbody td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--gray-100);
  vertical-align: middle; white-space: nowrap;
}
.wp-table tbody td.r { text-align: right; }
.wp-table tbody tr:last-child td { border-bottom: none; }
.wp-table tbody tr:hover td { background: #F8FAFC; }
.wp-td-pos  { color: var(--green-600); font-weight: 600; font-variant-numeric: tabular-nums; }
.wp-td-neg  { color: var(--red-600);   font-weight: 600; font-variant-numeric: tabular-nums; }
.wp-td-zero { color: var(--gray-400);  font-variant-numeric: tabular-nums; }
.wp-td-mv   { font-weight: 600; font-variant-numeric: tabular-nums; }
.wp-td-name { font-weight: 500; color: var(--ocean-800); }

/* ── 平台/大类标签 ────────────────────────────── */
.wp-tag {
  display: inline-flex; align-items: center;
  border-radius: 5px; padding: 2px 6px;
  font-size: 11px; font-weight: 600; white-space: nowrap;
}
.wp-tag-overseas   { background: var(--blue-100);  color: #1D4ED8; }
.wp-tag-domestic   { background: var(--amber-100); color: #92400E; }
.wp-tag-bank       { background: var(--green-100); color: #065F46; }
.wp-tag-thirdparty { background: #EDE9FE; color: #5B21B6; }
.wp-tag-class      { background: var(--gray-100);  color: var(--gray-700); margin-left: 3px; }

/* ── 右侧面板卡片 ─────────────────────────────── */
.wp-panel-dark {
  background: linear-gradient(135deg, #1B2A4A 0%, #0F1E35 100%);
  border-radius: var(--radius);
  padding: 18px 20px;
  box-shadow: var(--shadow-dark);
  margin-bottom: 12px;
  border: 1px solid rgba(255,255,255,0.06);
}
.wp-panel-card {
  background: #fff;
  border: 1px solid var(--gray-200);
  border-radius: var(--radius);
  padding: 16px 18px;
  box-shadow: var(--shadow-sm);
  margin-bottom: 12px;
}
.wp-panel-title {
  font-size: 12px; font-weight: 600; color: var(--gray-500);
  text-transform: uppercase; letter-spacing: 0.5px;
  margin-bottom: 10px;
}

/* ── 风险告警 ─────────────────────────────────── */
.wp-alert {
  display: flex; gap: 10px;
  padding: 12px 14px;
  background: #FFF5F5;
  border: 1px solid #FECACA;
  border-radius: 10px;
  margin-bottom: 8px;
}
.wp-alert-title { font-size: 13px; font-weight: 600; color: #991B1B; }
.wp-alert-body  { font-size: 12px; color: #B91C1C; margin-top: 3px; line-height: 1.5; }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# 1. KPI 卡片组件
# ═══════════════════════════════════════════════════════════════════

def kpi_primary(label: str, value: str, sub_items: list[tuple[str, str]]):
    """
    主 KPI 卡片（深蓝渐变背景）。
    用于：总资产等最核心指标。

    Args:
        label:     卡片标签，如 "总资产（投资）"
        value:     核心数值，如 "¥1,296,225"
        sub_items: 次要信息列表，如 [("净资产", "¥996,225"), ("负债", "¥300,000")]
    """
    sub_html = "".join(
        f'<div><span>{k}</span> <strong>{v}</strong></div>'
        for k, v in sub_items
    )
    st.markdown(f"""
<div class="wp-kpi-primary">
  <div class="label">{label}</div>
  <div class="value">{value}</div>
  <div class="sub">{sub_html}</div>
</div>
""", unsafe_allow_html=True)


def kpi_secondary(label: str, value: str, value_class: str = "", delta: str = "", delta_class: str = ""):
    """
    次级 KPI 卡片（白底）。
    用于：浮动盈亏等次要指标。

    Args:
        label:       卡片标签，如 "浮动盈亏"
        value:       核心数值，如 "+¥2,508,727"
        value_class: 数值颜色类，可选 "pos" / "neg" / ""
        delta:       次要说明，如 "↑ 收益率 +193.5%"
        delta_class: 说明颜色类，可选 "pos" / "neg" / "warn" / ""
    """
    st.markdown(f"""
<div class="wp-kpi-secondary">
  <div class="label">{label}</div>
  <div class="value {value_class}">{value}</div>
  <div class="delta {delta_class}">{delta}</div>
</div>
""", unsafe_allow_html=True)


def kpi_tertiary(label: str, value: str, value_class: str = "", delta: str = "", delta_class: str = ""):
    """
    辅助 KPI 卡片（白底，字号略小）。
    用于：杠杆率等辅助指标。

    Args:
        label:       卡片标签，如 "杠杆率"
        value:       核心数值，如 "23.1%"
        value_class: 数值颜色类，可选 "alert" / ""
        delta:       次要说明，如 "▲ 超限 +22.8%"
        delta_class: 说明颜色类，可选 "neg" / ""
    """
    st.markdown(f"""
<div class="wp-kpi-tertiary">
  <div class="label">{label}</div>
  <div class="value {value_class}">{value}</div>
  <div class="delta {delta_class}">{delta}</div>
</div>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# 2. 通用卡片容器
# ═══════════════════════════════════════════════════════════════════

def card_start(title: str, icon: str = "", badge: str = ""):
    """
    开始一个通用卡片容器（需要配合 card_end() 使用）。
    由于 Streamlit 的渲染机制，卡片内容通过 st.markdown() 直接输出。

    Args:
        title: 卡片标题
        icon:  标题前的图标，如 "📊"
        badge: 右侧辅助说明，如 "12 只持仓"
    """
    badge_html = f'<span class="wp-card-badge">{badge}</span>' if badge else ""
    st.markdown(f"""
<div class="wp-card">
  <div class="wp-card-title">{icon} {title}{badge_html}</div>
""", unsafe_allow_html=True)


def card_end():
    """关闭通用卡片容器。"""
    st.markdown("</div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# 3. 资产配置偏差视图组件
# ═══════════════════════════════════════════════════════════════════

def asset_deviation_chart(items: list[dict]):
    """
    资产配置偏差视图（区间条 + 当前值点 + 偏差标签）。
    这是 v1.0 标准方案，禁止替换为双柱状图。

    Args:
        items: 配置项列表，每项包含：
            {
                "name": "权益",           # 资产类别名称
                "current": 71.7,          # 当前配置百分比（0-100）
                "target_min": 40.0,       # 目标区间下限
                "target_max": 80.0,       # 目标区间上限
                "color": "#F59E0B",       # 当前值圆点颜色
            }

    示例：
        asset_deviation_chart([
            {"name": "权益", "current": 71.7, "target_min": 40, "target_max": 80, "color": "#F59E0B"},
            {"name": "固收", "current": 19.3, "target_min": 20, "target_max": 60, "color": "#3B82F6"},
        ])
    """
    rows_html = ""
    for item in items:
        name = item["name"]
        current = item["current"]
        tmin = item["target_min"]
        tmax = item["target_max"]
        color = item.get("color", "#3B82F6")
        tmid = (tmin + tmax) / 2

        # 偏差判断
        if current < tmin:
            diff = current - tmid
            badge_class = "under"
            badge_text = f"↓ 低配 {diff:+.1f}%"
        elif current > tmax:
            diff = current - tmid
            badge_class = "over"
            badge_text = f"↑ 超配 {diff:+.1f}%"
        else:
            badge_class = "ok"
            badge_text = "✓ 区间内"

        rows_html += f"""
<div class="wp-deviation-item">
  <div class="wp-dev-name">{name}</div>
  <div class="wp-dev-bar-wrap">
    <div class="wp-dev-bar-range" style="left:{tmin}%;width:{tmax - tmin}%"></div>
    <div class="wp-dev-bar-mid"   style="left:{tmid}%"></div>
    <div class="wp-dev-bar-dot"   style="left:{current}%;background:{color}"></div>
  </div>
  <div class="wp-dev-current">{current:.1f}%</div>
  <div><span class="wp-dev-badge {badge_class}">{badge_text}</span></div>
</div>"""

    legend_html = """
<div style="display:flex;gap:14px;margin-bottom:12px;font-size:11px;color:var(--gray-400)">
  <div style="display:flex;align-items:center;gap:5px">
    <div style="width:18px;height:7px;background:rgba(59,130,246,0.14);border-radius:3px;border:1px solid rgba(59,130,246,0.28)"></div>目标区间
  </div>
  <div style="display:flex;align-items:center;gap:5px">
    <div style="width:11px;height:11px;border-radius:50%;background:#3B82F6;border:2px solid white;box-shadow:0 0 0 1px rgba(0,0,0,0.12)"></div>当前配置
  </div>
  <div style="display:flex;align-items:center;gap:5px">
    <div style="width:2px;height:11px;background:rgba(59,130,246,0.45);border-radius:1px"></div>目标中值
  </div>
</div>"""

    st.markdown(f"""
<div class="wp-card">
  <div class="wp-card-title">📊 大类资产配置
    <span style="font-size:11px;font-weight:400;color:var(--gray-400);margin-left:2px">当前 vs 目标区间</span>
  </div>
  {legend_html}
  {rows_html}
</div>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# 4. 金融风格表格组件
# ═══════════════════════════════════════════════════════════════════

# 平台标签类型映射
PLATFORM_TAG_CLASS = {
    "老虎证券": "wp-tag-overseas",
    "富途证券": "wp-tag-overseas",
    "国金证券": "wp-tag-domestic",
    "招商银行": "wp-tag-bank",
    "建设银行": "wp-tag-bank",
    "支付宝":   "wp-tag-thirdparty",
    "微信理财": "wp-tag-thirdparty",
}


def format_pnl_cell(value: float, is_pct: bool = False) -> str:
    """将盈亏数值格式化为带颜色的 HTML 单元格内容。"""
    if value > 0:
        text = f"+{value:.2f}%" if is_pct else f"+¥{value:,.0f}"
        return f'<span class="wp-td-pos">{text}</span>'
    elif value < 0:
        text = f"{value:.2f}%" if is_pct else f"-¥{abs(value):,.0f}"
        return f'<span class="wp-td-neg">{text}</span>'
    else:
        text = "0.00%" if is_pct else "¥0"
        return f'<span class="wp-td-zero">{text}</span>'


def platform_tag(platform: str) -> str:
    """生成平台标签 HTML。"""
    tag_class = PLATFORM_TAG_CLASS.get(platform, "wp-tag-class")
    return f'<span class="wp-tag {tag_class}">{platform}</span>'


def asset_class_tag(asset_class: str) -> str:
    """生成资产大类标签 HTML。"""
    return f'<span class="wp-tag wp-tag-class">{asset_class}</span>'


def holdings_table(holdings: list[dict]):
    """
    金融风格持仓明细表格。
    数值列右对齐，盈亏红绿着色，hover 高亮。

    Args:
        holdings: 持仓列表，每项包含：
            {
                "platform":    "老虎证券",
                "name":        "理想汽车 LI",
                "code":        "LI",
                "asset_class": "权益",
                "position":    2500,
                "mv_usd":      43325.0,   # 可为 None
                "mv_hkd":      None,      # 可为 None
                "mv_cny":      298943.0,
                "weight_pct":  23.06,
                "pnl_cny":     -11044.0,
                "pnl_pct":     -3.70,
            }
    """
    rows_html = ""
    for h in holdings:
        mv_usd = f"${h['mv_usd']:,.0f}" if h.get("mv_usd") else "—"
        mv_hkd = f"HK${h['mv_hkd']:,.0f}" if h.get("mv_hkd") else "—"
        mv_cny = f"¥{h['mv_cny']:,.0f}"
        pnl_html = format_pnl_cell(h["pnl_cny"])
        pnl_pct_html = format_pnl_cell(h["pnl_pct"], is_pct=True)

        rows_html += f"""
<tr>
  <td>{platform_tag(h['platform'])}</td>
  <td class="wp-td-name">{h['name']}</td>
  <td style="color:var(--gray-400);font-size:12px">{h.get('code','—')}</td>
  <td>{asset_class_tag(h['asset_class'])}</td>
  <td class="r">{h['position']:,}</td>
  <td class="r">{mv_usd}</td>
  <td class="r">{mv_hkd}</td>
  <td class="r wp-td-mv">{mv_cny}</td>
  <td class="r" style="color:var(--gray-500);font-size:12px">{h['weight_pct']:.2f}%</td>
  <td class="r">{pnl_html}</td>
  <td class="r">{pnl_pct_html}</td>
</tr>"""

    st.markdown(f"""
<div class="wp-card" style="padding:20px 20px 16px">
  <div class="wp-card-title">📋 资产明细
    <span class="wp-card-badge">{len(holdings)} 只持仓</span>
  </div>
  <div style="overflow-x:auto;margin:0 -20px;padding:0 20px">
    <table class="wp-table">
      <thead>
        <tr>
          <th>平台</th><th>资产名称</th><th>代码</th><th>大类</th>
          <th class="r">头寸</th><th class="r">市值(美元)</th>
          <th class="r">市值(港币)</th><th class="r">市值(人民币)</th>
          <th class="r">占比%</th><th class="r">盈亏(人民币)</th><th class="r">盈亏%</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
</div>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# 5. 风险告警组件
# ═══════════════════════════════════════════════════════════════════

def risk_alerts(alerts: list[dict]):
    """
    风险告警卡片组件。

    Args:
        alerts: 告警列表，每项包含：
            {
                "title": "[纪律触发] 单一持仓超限：理想汽车 LI",
                "body":  "理想汽车 LI 占总资产 23.06%，超过单一持仓上限 15.0%。"
            }
    """
    if not alerts:
        return
    items_html = "".join(f"""
<div class="wp-alert">
  <div style="font-size:15px;flex-shrink:0;margin-top:1px">🔴</div>
  <div>
    <div class="wp-alert-title">{a['title']}</div>
    <div class="wp-alert-body">{a['body']}</div>
  </div>
</div>""" for a in alerts)

    st.markdown(f"""
<div class="wp-card" style="border-color:#FECACA;background:#FFFBFB">
  <div class="wp-card-title" style="color:#991B1B">
    🔴 风险告警
    <span class="wp-card-badge" style="color:var(--red-600)">{len(alerts)} 项高风险</span>
  </div>
  {items_html}
</div>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# 6. 右侧面板组件
# ═══════════════════════════════════════════════════════════════════

def panel_total_asset(total: str, net_asset: str, floating_pnl: str, show: bool = True):
    """
    右侧面板 — 深蓝总资产卡片。

    Args:
        total:        总资产，如 "¥1,296,225"
        net_asset:    净资产，如 "¥996,225"
        floating_pnl: 浮动盈亏，如 "+¥2,508,727"
        show:         是否显示（在投资账户总览页面传 False）
    """
    if not show:
        return
    st.markdown(f"""
<div class="wp-panel-dark">
  <div style="font-size:11px;color:rgba(255,255,255,0.5);margin-bottom:4px">📈 总资产（投资）</div>
  <div style="font-size:24px;font-weight:700;color:#fff;letter-spacing:-0.8px;font-variant-numeric:tabular-nums;margin-bottom:12px;line-height:1.2">{total}</div>
  <div style="display:flex;gap:8px;flex-wrap:wrap">
    <span style="background:rgba(255,255,255,0.12);color:rgba(255,255,255,0.8);border-radius:6px;padding:3px 9px;font-size:11px;font-weight:500">净资产 {net_asset}</span>
    <span style="background:rgba(22,163,74,0.22);color:#4ADE80;border-radius:6px;padding:3px 9px;font-size:11px;font-weight:500">浮盈 {floating_pnl}</span>
  </div>
</div>
""", unsafe_allow_html=True)


def panel_alloc_list(title: str, icon: str, items: list[dict]):
    """
    右侧面板 — 分布进度条 + 占比列表。
    用于：大类资产配置、平台分布等。

    Args:
        title: 标题，如 "大类资产配置"
        icon:  图标，如 "🥧"
        items: 分布项列表，每项包含：
            {"name": "权益", "pct": 71.7, "color": "#F59E0B"}
    """
    bar_html = "".join(
        f'<div style="flex:{i["pct"]};background:{i["color"]};height:100%"></div>'
        for i in items
    )
    rows_html = "".join(f"""
<div style="display:flex;align-items:center;justify-content:space-between;padding:4px 0">
  <div style="display:flex;align-items:center;gap:7px">
    <div style="width:7px;height:7px;border-radius:50%;background:{i['color']};flex-shrink:0"></div>
    <span style="font-size:13px;color:var(--gray-500)">{i['name']}</span>
  </div>
  <span style="font-size:13px;font-weight:600;color:var(--ocean-800);font-variant-numeric:tabular-nums">{i['pct']:.2f}%</span>
</div>""" for i in items)

    st.markdown(f"""
<div class="wp-panel-card">
  <div class="wp-panel-title">{icon} {title}</div>
  <div style="height:7px;border-radius:4px;overflow:hidden;display:flex;gap:1px;margin-bottom:10px">{bar_html}</div>
  {rows_html}
</div>
""", unsafe_allow_html=True)


def panel_holdings_list(holdings: list[dict], total_count: int):
    """
    右侧面板 — 持仓明细列表（前 N 条）。

    Args:
        holdings:    前 N 条持仓，每项包含：
            {"name": "理想汽车 LI", "platform": "老虎证券", "asset_class": "权益",
             "mv_cny": 298943, "weight_pct": 23.06}
        total_count: 总持仓数量
    """
    rows_html = ""
    for h in holdings:
        tag_class = PLATFORM_TAG_CLASS.get(h["platform"], "wp-tag-class")
        rows_html += f"""
<div style="display:flex;align-items:flex-start;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--gray-100)">
  <div style="flex:1;min-width:0">
    <div style="font-size:13px;font-weight:500;color:var(--ocean-800);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{h['name']}</div>
    <div style="font-size:11px;color:var(--gray-400);margin-top:2px;display:flex;align-items:center;gap:4px">
      <span class="wp-tag {tag_class}" style="font-size:10px;padding:1px 5px">{h['platform']}</span> {h['asset_class']}
    </div>
  </div>
  <div style="text-align:right;flex-shrink:0;margin-left:10px">
    <div style="font-size:13px;font-weight:600;color:var(--ocean-800);font-variant-numeric:tabular-nums">¥{h['mv_cny']:,.0f}</div>
    <div style="font-size:11px;color:var(--gray-400);margin-top:1px">{h['weight_pct']:.2f}%</div>
  </div>
</div>"""

    remaining = total_count - len(holdings)
    more_html = ""
    if remaining > 0:
        more_html = f'<div style="text-align:center;font-size:11px;color:var(--gray-400);padding-top:8px;border-top:1px solid var(--gray-100);margin-top:4px">还有 {remaining} 只持仓 · 查看全部</div>'

    st.markdown(f"""
<div class="wp-panel-card">
  <div class="wp-panel-title">📋 持仓明细 <span style="font-weight:400;color:var(--gray-400)">{total_count}只</span></div>
  {rows_html}
  {more_html}
</div>
""", unsafe_allow_html=True)
