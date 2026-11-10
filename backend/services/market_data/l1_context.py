"""Deterministic L1 prompt projection. Original Evidence remains the authority.

No sources, model calls, policy mutations or new financial computations here.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import tempfile

HARD_TOKENS = 40000
TARGET_TOKENS = 28000
FRAME_RESERVE = 1024
RECORD_SECTIONS = ('financial_operating_cashflow','competition_counterevidence','valuation_inputs','technical_events')
CORE_QUARTER_FIELDS = {'revenue_quarter','operating_income_quarter','net_income_quarter',
    'net_income_attributable_parent_quarter','net_income_available_to_ordinary_shareholders_quarter',
    'operating_cash_flow_quarter','capital_expenditures_quarter','free_cash_flow_quarter'}
VOCAB_URL = 'https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken'
VOCAB_SHA = '446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d'


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


@lru_cache(maxsize=1)
def _encoder():
    # Never fetch a tokenizer vocabulary while an analysis is running. A build
    # can prewarm the official reference vocabulary. Missing assets use a
    # conservative UTF-8 byte upper bound, never an optimistic chars/4 guess.
    try:
        import tiktoken
        from tiktoken import registry
        if 'o200k_base' in registry.ENCODINGS:
            return registry.ENCODINGS['o200k_base']
        cache = Path(os.getenv('TIKTOKEN_CACHE_DIR') or os.getenv('DATA_GYM_CACHE_DIR') or
                     str(Path(tempfile.gettempdir()) / 'data-gym-cache'))
        vocab = cache / hashlib.sha1(VOCAB_URL.encode()).hexdigest()
        if vocab.is_file() and hashlib.sha256(vocab.read_bytes()).hexdigest() == VOCAB_SHA:
            return tiktoken.get_encoding('o200k_base')
    except (ImportError, OSError, ValueError):
        pass
    return None


def measure(text):
    encoder = _encoder()
    return dict(bytes=len(text.encode('utf-8')),
        tokens=len(encoder.encode(text, disallowed_special=())) if encoder else len(text.encode('utf-8')),
        counting='o200k_base_reference_estimate' if encoder else 'utf8_byte_upper_bound')


class ContextBudgetExceeded(ValueError):
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__('L1_CONTEXT_BUDGET_EXCEEDED')


def _refs(value, index):
    if isinstance(value, str):
        return {value} if value in index else set()
    if isinstance(value, dict):
        return set().union(*(_refs(x, index) for x in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_refs(x, index) for x in value), set())
    return set()


def _conditional(value):
    result = deepcopy(value or {})
    # Only the already-computed repeated annual arrays are omitted. Every
    # scenario endpoint, assumption, input receipt and sensitivity is retained.
    cases = list(result.get('scenarios', {}).values())
    cases += [row['result'] for row in result.get('sensitivity', [])
              if isinstance(row,dict) and isinstance(row.get('result'),dict)]
    for case in cases:
        if isinstance(case.get('years'), list):
            case['annual_detail_in_full_evidence'] = True
            case.pop('years')
    return result


def _record_tables(groups):
    """Lossless key deduplication, not evidence ranking or truncation.

    project() omits None-valued outer fields, so null cells denote absent keys.
    Nested values (including explicit nulls) remain unchanged. Full refs stay
    verbatim, and the final output owner continues to use the original index.
    """
    result = deepcopy(groups)
    for section in RECORD_SECTIONS:
        rows = groups[section]
        if len(rows)<2 or any(value is None for row in rows for value in row.values()):
            continue
        columns = list(dict.fromkeys(key for row in rows for key in row))
        table = dict(columns=columns, rows=[[row.get(key) for key in columns] for row in rows],
                     null_cells='absent_field')
        if measure(dumps(table))['tokens'] < measure(dumps(rows))['tokens']:
            result[section] = table
    return result


def project(assessment):
    from backend.services.market_data.output_policy import _evidence_index
    index = _evidence_index(assessment)
    records = {(r.get('record_id') or r['source_url']): r for r in index.values()}
    canonical = {ref: (r.get('record_id') or r['source_url']) for ref, r in index.items()}
    quarters = sorted({r.get('period') for r in records.values()
                       if r.get('scope') in {'quarter','quarterly'} and r.get('period') and
                       str(r.get('field','')).endswith('_quarter')}, reverse=True)
    periods = set(quarters[:2])
    core_periods = set(quarters[:4])
    if quarters:
        comparable = str(int(quarters[0][:4])-1)+quarters[0][4:]
        if comparable in quarters: periods.add(comparable)
    annual = max((r.get('period') or '' for r in records.values() if r.get('scope')=='annual'), default='')
    selected = set()
    for ref, row in records.items():
        if (row['kind'] in {'research','event','technical','research_metric'} or row.get('period') in periods
                or (row.get('period') in core_periods and row.get('field') in CORE_QUARTER_FIELDS)):
            selected.add(ref)
        elif row.get('scope')=='annual' and row.get('period')==annual:
            selected.add(ref)
    # Dependency closure is not top-K: TTM/FX/ADS/comparative evidence must not
    # disappear just because its source period is older than the current window.
    selected.update(canonical[r] for r in _refs(assessment.get('conditional_valuation'), index))
    pending = list(selected)
    while pending:
        row = records[pending.pop()]
        for ref in _refs(row.get('evidence_refs', []), index):
            key = canonical[ref]
            if key not in selected:
                selected.add(key); pending.append(key)
    sources = {}
    source_keys = {}
    dependencies = {}
    dependency_keys = {}
    context_ids={ref:'e'+str(n) for n,ref in enumerate(sorted(selected))}
    def intern(value):
        if isinstance(value,str) and value in canonical and canonical[value] in context_ids:
            return {'context_id':context_ids[canonical[value]]}
        if isinstance(value,list):return [intern(x) for x in value]
        if isinstance(value,dict):return {k:intern(v) for k,v in value.items()}
        return value
    groups = {key:[] for key in ('financial_operating_cashflow','competition_counterevidence','valuation_inputs','technical_events')}
    for ref in sorted(selected):
        row = records[ref]
        source = {k:row.get(k) for k in ('source','source_url','published_at','fetched_at')}
        source_key = dumps(source)
        source_id = source_keys.setdefault(source_key, 's'+str(len(source_keys)))
        sources[source_id] = source
        values = row.get('values') or {}
        if row['kind'] == 'technical':
            values = {p:{k:v.get(k) for k in ('record_id','start_at','end_at','currency','market','indicators')}
                      for p,v in values.get('series',{}).items() if isinstance(v,dict)}
        elif row['kind'] in {'research','event'}:
            # Official structured observations already have independent field
            # receipts below. Never duplicate entire observations/filing tables.
            values = {k:values[k] for k in ('text','content','topic') if values.get(k)}
        else:
            values = {k:v for k,v in values.items() if not isinstance(v,(dict,list))}
        dependency = row.get('evidence_refs',[])
        dependency_key = dumps(dependency)
        dependency_id = dependency_keys.setdefault(dependency_key, 'd'+str(len(dependency_keys)))
        if dependency:dependencies[dependency_id]=intern(dependency)
        detail = row.get('details') or {}
        record = dict(evidence_ref=ref, context_id=context_ids[ref], source_ref=source_id, kind=row['kind'],
            symbol=row.get('symbol'), field=row.get('field'), period=row.get('period'),
            data_as_of=row.get('data_as_of'), scope=row.get('scope'), currency=row.get('currency'),
            unit=row.get('unit'), accounting_basis=row.get('accounting_basis'),
            fiscal_period_start=row.get('fiscal_period_start'),
            ordinary_shares_per_ads=row.get('ordinary_shares_per_ads'), issuer_type=row.get('issuer_type'),
            historical_only=row.get('historical_only',False),
            values=values, numeric_fields=sorted(k for k,v in values.items()
                if isinstance(v,(int,float)) and not isinstance(v,bool)),
            dependency_set=dependency_id if dependency else None,
            calculation={k:detail[k] for k in ('formula','comparison_period','technical_period') if k in detail})
        if assessment.get('symbol') and row.get('symbol')==assessment['symbol']:
            record.pop('symbol')  # Single verified subject is declared once below.
        group = ('competition_counterevidence' if row['kind']=='research' else
                 'technical_events' if row['kind'] in {'technical','event'} else
                 'valuation_inputs' if row.get('field') in {'diluted_eps_ttm','observed_price','fx_rate','pe_ttm','earnings_yield_pct','ordinary_shares_per_ads'} else
                 'financial_operating_cashflow')
        groups[group].append({k:v for k,v in record.items() if v is not None and v != {}})
    groups['symbol'] = assessment.get('symbol')
    groups['record_quality'] = 'valid'
    groups['conditional_valuation'] = intern(_conditional(assessment.get('conditional_valuation')))
    groups['sources'] = sources
    groups['dependencies'] = dependencies
    groups['constraints_intent'] = {k:deepcopy(assessment.get(k)) for k in
        ('qualification','issues','infoNeeded','research_intent','planner','confidence_ceiling')}
    groups['constraints_intent']['financial_limitations']=(assessment.get('financial_research') or {}).get('limitations',[])
    groups['constraints_intent']['valuation_limitations']={k:deepcopy(v) for k,v in
        (assessment.get('valuation_inputs') or {}).items() if k in {'missing','limitations','issues'}}
    intent=groups['constraints_intent'].get('research_intent') or {}
    assumptions=(assessment.get('conditional_valuation') or {}).get('assumptions')
    if assumptions is not None and intent.get('conditional_valuation') == assumptions:
        intent['conditional_valuation']={'context_reference':'conditional_valuation.assumptions'}
    groups['constraints_intent']['unqualified_conflicts']=[
        {k:r.get(k) for k in ('field','period','reason','issue','sources') if k in r}
        if isinstance(r,dict) else str(r) for r in (assessment.get('source_conflicts') or [])]
    allocation=assessment.get('allocation_context') or {}
    groups['constraints_intent']['stage_target_context']={k:deepcopy(allocation.get(k)) for k in
        ('confirmed_stage_targets','proposed_stage_targets','target_confirmation_status','effective_rules_provenance')}
    diagnostics = dict(schema_version='wealthpilot.l1-context.v1',
        eligible_records=len(records), selected_records=len(selected),
        excluded_historical_records=len(records)-len(selected),
        dependency_refs_present=all(canonical[r] in selected for r in _refs(assessment.get('conditional_valuation'), index)),
        sections={k:measure(dumps(v)) for k,v in groups.items()},
        original_assessment=measure(dumps(assessment)),
        target_tokens=TARGET_TOKENS, hard_tokens=HARD_TOKENS,
        provider_token_count='UNKNOWN_UNTIL_RESPONSE', model_tokenizer_mapping='UNKNOWN_REFERENCE_ENCODING',
        execution_authorized=False)
    return groups, diagnostics


def messages(assessment, question, *, policy, holding, history=(), research_run=None):
    from backend.services.market_data.output_policy import explanation_prompt
    groups, diagnostics = project(assessment)
    if research_run is not None:
        from .l1_research_v2 import PROMPT
        research_run.prepare(groups, assessment)
        system = PROMPT
        # Use an actual selected Owner field; an invented example ref can
        # otherwise be mistaken for something the request authorized.
        examples = (row for row in groups['financial_operating_cashflow']
                    if row.get('field') == 'revenue_yoy_pct' and row['field'] in row['numeric_fields'])
        example = next(examples, None)
        if example:
            ref, field = example['evidence_ref'], example['field']
            system += '\n真实可用数字绑定示例（只示范绑定，论点仍须论证）：' + dumps(dict(
                text_template='最近可比期间的集团收入同比为{{n1}}，持续性仍需验证。',
                evidence_refs=[ref], numbers=[dict(id='n1', evidence_ref=ref, field=field)]))
    else:
        # Reuse the actual selected facts in the original binding/safety contract
        # once, without duplicating their serialized payload. Final qualification
        # always uses the original complete assessment.
        context_records=[r for key in ('financial_operating_cashflow','competition_counterevidence','valuation_inputs','technical_events') for r in groups[key]]
        contract = explanation_prompt(assessment,context_records=context_records,include_data=False)
        contract = contract.replace('保留原决策JSON，同时新增professional_explanation数组','仅输出professional_explanation数组')
        system = ('你是原PEER的L1单标专业投研Expressing。只返回JSON对象professional_explanation。'
            '不生成旧DecisionResult、交易参数或订单。经营、竞争、投资逻辑、风险和复核条件优先；'
            '有退出意图时讨论exit_rationale；技术证据仅在相关且有资格时解释。'
            '持仓事实、用户假设、历史事实、条件估值和未来推论必须分开。'
            'symbol为本包共同的已核验证券主体；record_quality是本包各记录由原Owner授予的共同引用质量，不可升级L2–L4；source_ref引用sources时钟；dependency_set引用dependencies；context_id只是本包内部关联，不是合格引用。合格引用必须复制完整evidence_ref。numeric_fields列出可绑定字段；values是原值，单位/币种见同行，展示数字由服务端替换。'
            '不得自行升级资格或重算数字。条件情景不是公允价值或未来事实。\n'+contract)
    system += ('\n主题记录如使用columns/rows，则每行按相同列名读取；null_cells=absent_field表示该列未知/未提供。'
               'evidence_ref原文仍是唯一引用，列号及context_id均不是证据引用或数字字段名。')
    policy=deepcopy(policy)
    for rule in policy.get('rules',[]):
        if isinstance(rule,dict) and isinstance(rule.get('source_clause'),dict):
            rule['source_clause']={k:v for k,v in rule['source_clause'].items() if k!='text'}
            rule['source_clause']['full_text_in_policy_owner']=True
    user_history=list(dict.fromkeys(t['content'] for t in history if isinstance(t,dict) and
        t.get('role')=='user' and isinstance(t.get('content'),str) and t['content']!=question))
    groups['request_policy_holding'] = dict(question=question,policy=policy,holding=holding,
        unverified_user_research_context=user_history)
    payload = [{'role':'system','content':system},{'role':'user','content':dumps(groups)}]
    before = measure(dumps(payload))
    diagnostics['projection_stages']=[dict(stage='topic_projection', **before)]
    diagnostics['record_encoding']='objects'
    # A real soft-target action: share repeated record keys without deleting a
    # single selected fact, dependency, user turn or policy constraint.
    if before['tokens']+FRAME_RESERVE > TARGET_TOKENS:
        compact = _record_tables(groups)
        candidate = [payload[0],dict(role='user',content=dumps(compact))]
        after = measure(dumps(candidate))
        if after['tokens'] < before['tokens']:
            groups, payload = compact, candidate
            diagnostics['record_encoding']='columns_rows_v1'
            diagnostics['projection_stages'].append(dict(stage='lossless_record_tables', **after))
    diagnostics['sections']={key:measure(dumps(value)) for key,value in groups.items()}
    diagnostics['sections']['system_contract']=measure(system)
    diagnostics['request']=measure(dumps(payload))
    diagnostics['saved_reference_tokens']=before['tokens']-diagnostics['request']['tokens']
    diagnostics['estimated_with_frame_reserve']=diagnostics['request']['tokens']+FRAME_RESERVE
    diagnostics['status']='accepted' if diagnostics['estimated_with_frame_reserve']<=HARD_TOKENS else 'budget_exceeded'
    if diagnostics['status']=='budget_exceeded':raise ContextBudgetExceeded(diagnostics)
    return payload, diagnostics


def compact_facts_text(assessment):
    """Display only; all qualified records/calculations stay in the full artifact."""
    from backend.services.market_data.output_policy import _evidence_index
    from backend.services.market_data.financial_research import display
    rows = {(r.get('record_id') or r['source_url']):r for r in _evidence_index(assessment).values()}
    priorities = ('revenue_quarter','revenue','net_income_quarter','net_income','operating_income_quarter',
        'operating_income','gross_margin_pct','operating_cash_flow_quarter','ocf_quarter','operating_cash_flow','capital_expenditures_quarter','capex_quarter','free_cash_flow_quarter','fcf_quarter',
        'revenue_yoy_pct','net_income_yoy_pct','ocf_to_net_income_pct','diluted_eps_ttm')
    chosen=[]
    for field in priorities:
        candidates=[r for r in rows.values() if r.get('field')==field or field in r.get('values',{})]
        if candidates:
            row=max(candidates,key=lambda r:str(r.get('period') or r.get('data_as_of') or ''))
            chosen.append((row,field))
        if len(chosen)>=10:break
    lines=['专业推断未完成；以下仅为已核验事实，不构成投资判断或交易授权。']
    for row,field in chosen:
        lines.append(('- 历史基线（非当前经营事实）：' if row.get('historical_only') else '- ')+display(row,field)+'；来源 '+str(row.get('source'))+'；发布 '+str(row.get('published_at') or 'UNKNOWN'))
    if not chosen:lines.append('已保留合格技术/研究来源，但没有可展示的合格关键财务字段。')
    for period,technical in (assessment.get('technical_levels') or {}).items():
        summary={k:technical.get(k) for k in ('start_at','end_at','market','currency','record_id')}
        summary['indicators']={k.upper():v for k,v in (technical.get('indicators') or {}).items()}
        lines.append('合格'+period+'技术事实：'+dumps(summary)+'；仅限该周期，不代表经营改善。')
    conditional=assessment.get('conditional_valuation') or {}
    lines.append('条件估值：'+str(conditional.get('status','UNKNOWN'))+'；研究假设不是历史事实或公允价值。')
    for name,case in conditional.get('scenarios',{}).items():
        lines.append('- '+name+'：条件年化回报（小数） '+str(case.get('annualized_return','N/A'))+'；假设与完整敏感性见私有工件。')
    if conditional.get('missing'):lines.append('估值必要缺口：'+', '.join(str(x) for x in conditional['missing']))
    lines.append('完整财务、来源时钟、条件假设及计算明细保留于原私有完整工件。专业解释与复核尚未完成，不自动重跑。')
    return '\n'.join(lines)
