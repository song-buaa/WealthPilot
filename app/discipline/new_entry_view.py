"""
新建仓场景的纪律规则视图(v3.4 M8.5)。

不是 risk_engine 的扩展——risk_engine 对新建仓天然适用。
本模块只是把 _DEFAULT_RULES 中跟新建仓相关的规则,以人类可读形式输出,
让 ExpressingAgent 注入到 LLM prompt 里。

未来 v3.6 上 RAG 后,build_new_entry_discipline_summary 会被
"RAG 召回 + 排序"取代,但调用方(ExpressingAgent.payload)的接口不变。
"""
from __future__ import annotations


def build_new_entry_discipline_summary(rules: dict) -> str:
    """构造新建仓纪律的人类可读摘要。

    Args:
        rules: 完整的 discipline config dict(get_rules() 返回值)

    Returns:
        多行字符串,供 ExpressingAgent prompt 的 {rule_summary} 占位符注入
    """
    if not rules:
        return "【适用纪律(新建仓场景)】\n- 纪律配置暂缺"

    lines = ["【适用纪律(新建仓场景)】"]

    # 1. 单标的上限 + 建议区间
    single = rules.get("single_asset_limits", {})
    if single:
        max_pct = single.get("max_position_pct", 0.40) * 100
        preferred = single.get("preferred_position_range", [0.20, 0.30])
        lines.append(f"- 单标的上限: 建仓后单一标的不超过组合总市值的 {max_pct:.0f}%")
        if len(preferred) == 2:
            lines.append(
                f"- 建议仓位区间: 单标的合理仓位 {preferred[0]*100:.0f}%-{preferred[1]*100:.0f}%"
            )

    # 2. 建仓节奏(语义改写: "加仓" → "首次建仓")
    sizing = rules.get("position_sizing", {})
    if sizing:
        max_add = sizing.get("max_single_add_pct", 0.10) * 100
        batches = sizing.get("min_batches_required", 2)
        lines.append(
            f"- 首次建仓比例: 建议不超过总资产的 {max_add:.0f}%"
        )
        lines.append(
            f"- 分批建仓: 建议至少分 {batches} 批建仓,首批不超过目标仓位的 50%"
        )

    # 3. 流动性(子弹纪律)
    liquidity = rules.get("liquidity_limits", {})
    if liquidity:
        min_cash = liquidity.get("min_cash_pct", 0.20) * 100
        lines.append(f"- 流动性要求: 建仓后保留现金不低于总资产 {min_cash:.0f}%")

    # 4. 资产配置区间
    allocation = rules.get("asset_allocation_ranges", {})
    if allocation:
        eq_max = allocation.get("equity_max", 0.80) * 100
        lines.append(
            f"- 资产配置: 建仓不应导致权益类资产超过 {eq_max:.0f}% 上限"
        )

    # 5. 组合熔断
    circuit = rules.get("portfolio_circuit_breaker", {})
    if circuit:
        trigger = circuit.get("drawdown_trigger_pct", 0.25) * 100
        lines.append(
            f"- 组合熔断: 组合回撤超 {trigger:.0f}% 时禁止所有买入"
        )

    # 显式标注跳过的规则(审计依据)
    lines.append("")
    lines.append("【不适用规则(新建仓场景跳过)】")
    lines.append("- 杠杆分级: 新建仓不涉及杠杆工具")
    lines.append("- 再平衡偏离度: 适用于已持仓标的")
    lines.append("- 止损规则: 适用于已建仓后")
    lines.append("- 冷静期: 与单标的新建仓无直接关联")

    return "\n".join(lines)
