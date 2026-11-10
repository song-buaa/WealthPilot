"""Reader projection of the finalized L1 v2 receipt. No analysis or requalification."""
from decimal import Decimal, DecimalException
import re


CASE_NAMES = {'cautious': '谨慎', 'base': '基准', 'optimistic': '乐观'}
CAPITAL = {
    'not_supported_at_observed_price': '当前证据暂不支持在该历史观察价新增资本配置。',
    'insufficient_scenario_review': '情景论证未闭合，新增资本配置的证据不足。',
    'requires_separate_capital_judgment': '基准情景达到研究门槛，新增配置仍须独立评估风险补偿和替代机会。',
}
LIMITS = {
    'FUTURE_EARNINGS_GROWTH_SHARE_CHANGE_MULTIPLE_AND_PAYOUT_ARE_RESEARCH_ASSUMPTIONS': '未来盈利、股数、终值和分配均为研究假设。',
    'NO_VERIFIED_BUYBACK_OR_DILUTION_FORECAST': '未来回购和稀释未核实。',
    'POST_FILING_CORPORATE_ACTION_AND_FX_RAW_SOURCE_CLOSURE_NOT_IN_THIS_PROJECTION': '申报后的公司行动和完整汇率原始证据未在本报告闭合。',
    'CONDITIONAL_PE_VALUE_NOT_INTRINSIC_VALUE_OR_TARGET_PRICE': '条件结果仅用于研究，不能作为公允价值或目标价。',
    'TREASURY_CMT_IS_NOT_A_REALIZED_TOTAL_RETURN': '国债收益率仅用于比较，实际总回报未知。',
    'DISTRIBUTABLE_CASH_AND_FUTURE_NET_DILUTION_UNVERIFIED': '股东可分配现金和未来净稀释未知。',
    'MARKET_RISK_BENCHMARK_NOT_ISSUER_BETA_OR_CURRENT_SPOT_ESTIMATE': '市场风险资料未校准到发行人，也不代表实时风险参数。',
    'TERMINAL_ECONOMIC_CASH_ACCESS_AND_STEADY_GROWTH_ARE_ASSUMPTIONS': '终期现金可取得率和稳态增长仍待验证。',
    'OWNER_CASH_PROXY_NOT_QUARTER_FCF_OR_DISTRIBUTABLE_FCFE': '历史现金代理不等于季度自由现金流或可分配股东现金。',
}


def _percent(value):
    return '未知' if value is None else f'{Decimal(str(value)):.2%}'


def _number(value, digits=2):
    return '未知' if value is None else f'{Decimal(str(value)):.{digits}f}'


def _valuation(cells, status):
    if not cells:
        missing = status.get('missing') or []
        lines = ['当前缺少可用条件估值，相关回报及反向要求未知。']
        if missing:
            lines.append('缺失的输入或资格条件见审计视图。')
        if status.get('reverse_equation'):
            lines.extend(['', status['reverse_equation']])
        return lines
    lines = ['以下为五年条件回报。研究门槛不代表个人要求回报。', '',
             '| 情景 | 条件年化 | 达门槛所需盈利年增长 / 现金可取得率 |', '| --- | ---: | ---: |']
    for name, label in CASE_NAMES.items():
        cell = next((c for c in cells.values() if c['name'] == name), None)
        if not cell:
            continue
        result = cell['result']
        reverse = f'{_percent(result.get("implied_earnings_growth_no_dividends"))} / {_percent(result.get("implied_economic_access_at_hurdle"))}'
        if result.get('implied_year_five_ebit_margin') is not None:
            reverse = f'第五年营业利润率 {_percent(result["implied_year_five_ebit_margin"])}'
        lines.append(f'| {label} | {_percent(result["annualized_return"])} | {reverse} |')
    anchor = next((c['terminal_anchor'] for c in cells.values() if c.get('terminal_anchor')), None)
    if anchor:
        lines.extend(['', f'{anchor["period"]} 五年期美元国债收益率为 {_percent(anchor["usd_five_year_cmt"])}。'])
    lines.extend(['', '完整情景假设、历史现金桥、计算依据与敏感性见审计视图。'])
    return lines


def _review_theme(section, role, scenario_label):
    if section == '财务与现金兑现' and role == 'supporting':
        return '盈利兑现', 0
    if section == '机会成本':
        return '现金质量与股东可取得性', 0
    if section == '财务与现金兑现' or scenario_label == '基准':
        return '现金质量与股东可取得性', 1
    if section == '竞争与反证':
        return '竞争与监管', 0
    if section == '经营与商业模式':
        return '竞争与监管', 1
    return section, 2


def render_reader(final, symbol=None, *, policy_bound=False):
    """Return reader Markdown plus a positional citation key for the audit view."""
    cells = final['scenario_cells']
    claims = final['claims']
    citations, sections, reviews = {}, {}, {}
    # Bound claims keep their substantive text. A short list selects from their
    # existing review sentences; the audit view preserves the complete native text.
    for claim in claims:
        paragraph = claim['paragraph']
        body = paragraph['text']
        if not isinstance(body, str) or not body or not paragraph['evidence_refs']:
            raise ValueError('READER_CLAIM_INCOMPLETE')
        refs = list(dict.fromkeys(paragraph['evidence_refs']))
        marker = '〔' + '、'.join(str(citations.setdefault(ref, len(citations)+1)) for ref in refs) + '〕'
        questions = set(claim['question_ids'])
        scenario = claim['scenario_cell_refs']
        if claim['role'] == 'synthesis':
            section, label = '投资结论', ''
            if body == '当前值得继续验证长期投资命题。以下是这个判断成立及改变的必要前提。':
                body = '继续跟踪，验证长期投资命题。'
        elif 'Q6' in questions and questions <= {'Q6', 'Q7'}:
            section, label = '机会成本', ''
        elif scenario or 'Q4' in questions and 'Q3' not in questions:
            section = '情景依据'
            label = '、'.join(CASE_NAMES[cells[ref]['name']] for ref in scenario)
        elif 'Q2' in questions:
            section = '竞争与反证' if claim['role'] == 'counterclaim' else '经营与商业模式'
            label = ''
        elif 'Q1' in questions and claim['role'] == 'thesis':
            section, label = '投资命题', ''
        elif 'Q3' in questions:
            section, label = '财务与现金兑现', ''
        else:
            section, label = '风险与限制', ''
        trigger = re.search(r'(?<=。)(?:下次财报|下一次财报|以下次财报|以方案规定的阶段性评估窗口)[^。]*(?:。|$)', body) if 'Q7' in questions else None
        if trigger:
            theme, priority = _review_theme(section, claim['role'], label)
            previous = reviews.get(theme)
            if previous is None or priority < previous[0]:
                reviews[theme] = (priority, f'**{theme}**　{trigger.group()} {marker}')
            body = body[:trigger.start()] + body[trigger.end():]
        text = f'**{label}情景**　{body}' if label else body
        sections.setdefault(section, []).append(f'{text} {marker}')

    title = f'# {symbol} 投资研究报告' if isinstance(symbol, str) and re.fullmatch(r'[A-Z0-9.:-]{1,24}', symbol) else '# 投资研究报告'
    lines = [title, '', '## 投资结论', '']
    decision = (final.get('scenario_decision_review') or {}).get('incremental_capital')
    if decision in CAPITAL:
        lines.extend([f'**{CAPITAL[decision]}**', ''])
    if '投资结论' not in sections:
        lines.append('综合投资判断尚未闭合。')
    lines.extend(sections.get('投资结论', []))
    base = next((c for c in cells.values() if c['name'] == 'base'), None)
    reference_case = base or next(iter(cells.values()), None)
    if reference_case:
        price = reference_case['inputs'].get('price_usd_per_ads')
        date = (reference_case.get('receipts', {}).get('price') or {}).get('period')
        if price is not None and date:
            lines.extend(['', f'历史观察价 **{_number(price)} USD/ADS**（{date}，非实时）。'])
    if base:
        lines.extend(['', f'基准情景五年条件年化 **{_percent(base["result"]["annualized_return"])}**，'
                      f'研究门槛 **{_percent(base["required_return"])}**。'])
    elif reference_case:
        lines.extend(['', f'已核验情景的研究门槛 **{_percent(reference_case["required_return"])}**。'])
    for heading in ('投资命题', '经营与商业模式', '竞争与反证', '财务与现金兑现', '风险与限制'):
        if heading in sections:
            lines.extend(['', f'## {heading}', '', '\n\n'.join(sections[heading])])
    metrics = final.get('key_metrics') or []
    if metrics:
        lines.extend(['', '**关键财务与观察值**', ''])
        for row in metrics:
            ref = row.get('evidence_ref')
            marker = f' 〔{citations.setdefault(ref, len(citations)+1)}〕' if ref else ''
            value = row['value']
            if value == 'UNKNOWN':
                label = {'revenue_quarter': '季度收入', 'operating_income_quarter': '季度营业利润',
                         'net_income_quarter': '季度净利润', 'operating_cash_flow_quarter': '季度经营现金流',
                         'free_cash_flow_quarter': '季度自由现金流', 'revenue_yoy_pct': '季度收入同比',
                         'operating_margin_pct': '季度营业利润率', 'diluted_eps_ttm': '滚动摊薄每股收益',
                         'observed_price': '观察价'}.get(row['field'], '一项关键财务指标')
                value = label + '未知'
            lines.append(f'- {value}{marker}')
    valuation_status = final.get('valuation_status') or {}
    lines.extend(['', '## 条件估值与长期回报', '', *_valuation(cells, valuation_status)])
    for heading in ('情景依据', '机会成本'):
        if heading in sections:
            lines.extend(['', f'## {heading}', '', '\n\n'.join(sections[heading])])
    if '机会成本' not in sections:
        lines.extend(['', '同币种、期限与风险可比的机会成本论证仍待核验。'])
    lines.extend(['', '## 观察指标与复核条件', ''])
    if reviews:
        priorities = ('盈利兑现', '现金质量与股东可取得性', '竞争与监管')
        themes = [theme for theme in priorities if theme in reviews]
        themes.extend(theme for theme in reviews if theme not in themes)
        lines.append('\n\n'.join(reviews[theme][1] for theme in themes[:3]))
        lines.extend(['', '其余复核条件随原文保留在审计视图。'])
    elif any('Q7' in claim['question_ids'] for claim in claims):
        lines.append('复核条件见各项已核验论证。')
    else:
        lines.append('现有证据不足以确认具体复核条件。')
    codes = valuation_status.get('limitations') or []
    translated = [LIMITS[code] for code in codes if code in LIMITS]
    if translated or codes:
        lines.extend(['', '**估值与证据边界**', '', *[f'- {line}' for line in translated]])
        if len(translated) < len(codes):
            lines.append('- 其余模型适用限制见审计视图。')
    if policy_bound:
        lines.extend(['', '适用政策约束保留在审计收据。发行人和 ETF 穿透资格不足时，相关风险保持未知。'])
    lines.extend(['', '个人回报要求、未获资格的风险与持仓数据保持未知。此报告不授予交易权限。',
                  '编号对应审计视图中的证据收据。'])
    return '\n'.join(lines), {str(number): ref for ref, number in citations.items()}


def reader_projection(final, symbol=None, *, policy_bound=False):
    if not isinstance(final, dict) or not final.get('claims'):
        return None
    try:
        return render_reader(final, symbol, policy_bound=policy_bound)
    except (AttributeError, KeyError, TypeError, ValueError, DecimalException):
        # A saved older receipt can lack the reader's structured fields.
        # Its original qualified report remains the compatible delivery.
        return None
