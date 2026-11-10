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
    lines = ['以下为条件研究。历史观察价不代表当前报价，研究门槛不代表个人要求回报。']
    lines.extend(['', '| 情景 | 历史观察价 | 五年条件年化 | 研究门槛 | 达门槛所需盈利年增长 / 现金可取得率 |',
                  '| --- | ---: | ---: | ---: | ---: |'])
    ordered = sorted(cells.values(), key=lambda c: list(CASE_NAMES).index(c['name']))
    for cell in ordered:
        result = cell['result']
        inputs = cell['inputs']
        price = _number(inputs.get('price_usd_per_ads'))
        unit = 'USD/ADS'
        reverse = f'{_percent(result.get("implied_earnings_growth_no_dividends"))} / {_percent(result.get("implied_economic_access_at_hurdle"))}'
        if result.get('implied_year_five_ebit_margin') is not None:
            reverse = f'第五年营业利润率 {_percent(result["implied_year_five_ebit_margin"])}'
        lines.append(f'| {CASE_NAMES[cell["name"]]} | {price} {unit} | {_percent(result["annualized_return"])} | '
                     f'{_percent(cell["required_return"])} | {reverse} |')
    observation = (ordered[0].get('receipts', {}).get('price') or {}).get('period')
    as_of = ordered[0].get('decision_at')
    if observation:
        lines.extend(['', f'观察价日期 {observation}。'])
    if as_of:
        lines.append(f'估值证据截至 {as_of}。')
    lines.extend(['', '| 情景 | 盈利年增长 | 年股数变化 | 终值 | 分配假设 | 反向终期每 ADS 盈利 |',
                  '| --- | ---: | ---: | ---: | ---: | ---: |'])
    for cell in ordered:
        p = cell['parameters']
        if 'terminal_pe' in p:
            values = (_percent(p['earnings_growth']), _percent(p['annual_share_change']),
                      _number(p['terminal_pe']) + ' 倍 PE', _percent(p['dividend_payout']))
        else:
            values = ('收入年增 ' + _percent(p.get('annual_revenue_growth')),
                      '期末股数 ' + _number(p.get('year_five_ordinary_shares'), 0),
                      _number(p.get('terminal_ev_ebit_multiple')) + ' 倍 EV/EBIT', '详见收据')
        reverse_eps = cell['result'].get('implied_terminal_eps_no_dividends')
        lines.append('| ' + ' | '.join((CASE_NAMES[cell['name']], *values,
                                        _number(reverse_eps) + ' USD' if reverse_eps is not None else '未知')) + ' |')
    anchored = next((c for c in ordered if c.get('terminal_anchor')), None)
    if anchored:
        a = anchored['terminal_anchor']
        lines.extend(['', f'{a["period"]} 五年期美元国债收益率 {_percent(a["usd_five_year_cmt"])}。'])
        if anchored.get('multiple_basis'):
            lines.append(anchored['multiple_basis'])
        if anchored.get('assumption_rationale'):
            lines.append(anchored['assumption_rationale'])
    cash = next((c['owner_cash_basis'] for c in ordered if c.get('owner_cash_basis')), None)
    if cash:
        risk = cash['risk_source']
        lines.extend(['', f'风险资料版本 {risk["vintage"]}，成熟市场溢价 {_percent(risk["mature_equity_premium"])}，'
                      f'中国风险溢价 {_percent(risk["china_equity_country_premium"])}。',
                      '', '| 情景 | 稳态折现 | 稳态增长 | 现金转化 | 现金可取得 | 第六年现金代理 |',
                      '| --- | ---: | ---: | ---: | ---: | ---: |'])
        for cell in ordered:
            p = cell['owner_cash_parameters']
            result = cell['result']
            lines.append(f'| {CASE_NAMES[cell["name"]]} | {_percent(p["terminal_discount_rate"])} | '
                         f'{_percent(p["terminal_cash_growth"])} | {_percent(p["cash_conversion"])} | '
                         f'{_percent(p["economic_access_fraction"])} | {_number(result["terminal_owner_cash_proxy_usd_per_ads"])} USD/ADS |')
        lines.extend(['', '历史现金桥为集团人民币口径，每 ADS 按已核验换算作可比展示。',
                      '', '| 财年末 | 经营现金流 | 生产性资产支出 | 商家应付款现金贡献 | 股权激励经济成本 | 现金代理 | 每 ADS 现金代理 | 转化率 |',
                      '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |'])
        for row in cash['annual_history']:
            amounts = [_number(Decimal(row[k])/100_000_000) + ' 亿' for k in
                       ('operating_cash_cny', 'productive_asset_cash_spend_cny', 'merchant_payables_cash_change_cny',
                        'sbc_economic_cost_cny', 'owner_cash_proxy_cny')]
            lines.append('| ' + ' | '.join((row['period'], *amounts, _number(row['owner_cash_proxy_per_ads_cny']) + ' 元',
                                           _percent(row['conversion']))) + ' |')
    base = next((c for c in ordered if c['name'] == 'base'), {})
    if base.get('entry_price_sensitivity'):
        lines.extend(['', '**基准情景的假设入场价敏感性**（非行情）'])
        for row in base['entry_price_sensitivity']:
            lines.append(f'- {_number(row["hypothetical_price_usd_per_ads"])} USD/ADS，条件年化 '
                         f'{_percent(row["annualized_return"])}，反向所需盈利年增长 '
                         f'{_percent(row["implied_earnings_growth_no_dividends"])}。')
    return lines


def render_reader(final, symbol=None, *, policy_bound=False):
    """Return reader Markdown plus a positional citation key for the audit view."""
    cells = final['scenario_cells']
    claims = final['claims']
    citations, sections, reviews = {}, {}, []
    # Every finalized bound paragraph is kept exactly once, with a possible
    # sentence-boundary split to gather its existing review conditions.
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
        trigger = re.search(r'(?<=。)(?:下次财报|下一次财报|以下次财报|以方案规定的阶段性评估窗口)', body) if 'Q7' in questions else None
        if trigger:
            reviews.append(f'**{label or section}**　{body[trigger.start():]} {marker}')
            body = body[:trigger.start()]
        text = f'**{label}情景**　{body}' if label else body
        sections.setdefault(section, []).append(f'{text} {marker}')

    title = f'# {symbol} 投资研究报告' if isinstance(symbol, str) and re.fullmatch(r'[A-Z0-9.:-]{1,24}', symbol) else '# 投资研究报告'
    lines = [title, '', '## 投资结论']
    if '投资结论' not in sections:
        lines.append('综合投资判断尚未闭合。')
    lines.extend(sections.get('投资结论', []))
    decision = (final.get('scenario_decision_review') or {}).get('incremental_capital')
    if decision in CAPITAL:
        lines.extend(['', CAPITAL[decision]])
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
        lines.append('\n\n'.join(reviews))
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
