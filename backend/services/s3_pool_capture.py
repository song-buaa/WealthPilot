"""Explicit local raw capture using existing SEC/AV Owners, never brokers/LLMs."""
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
import hashlib
import io
import json
import os
import urllib.parse
import urllib.request

from backend.services import quality_valuation_research as archive


def now():return datetime.now(timezone.utc).isoformat()


def capture_pool(symbols,output,*,reuse=None,av_env_file=None):
    from research_v2 import sec_disclosures as sec
    from backend.services.s3_method_research import identity_projection
    from backend.services.market_data.av_fundamentals_service import AV_KEY_NAMES,get_next_av_key,_av_get,_response_status,response_diagnostic
    from backend.services.execution_plan.kline_provider import AVKlineProvider,decode_av_daily,decode_av_splits
    from backend.services.instruments.issuers import sec_ticker_matches
    if not 1<=len(symbols)<=20 or len(set(symbols))!=len(symbols):raise ValueError('POOL_SIZE_OR_DUPLICATE')
    import re
    if any(not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,14}:US',s) for s in symbols):raise ValueError('POOL_IDENTITY_UNSUPPORTED')
    output=Path(output)
    if output.exists():raise ValueError('CAPTURE_DIRECTORY_ALREADY_EXISTS')
    if av_env_file:
        # Explicit Dev-only selection of the existing AV Owner's allowed keys.
        # Never load broker, database, production or arbitrary dotenv settings.
        from dotenv import dotenv_values
        values=dotenv_values(av_env_file)
        for name in AV_KEY_NAMES:
            if values.get(name):os.environ[name]=values[name]
        values.clear()
    os.environ['PYTHON_DOTENV_DISABLED']='1'
    output.mkdir(parents=True)
    manifest=dict(schema_version='s3-d.sources.v1',companies={},requests=[],started_at=now())
    previous=archive._load(reuse,'manifest.json') if reuse else {}
    def save():
        (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    def store(raw,url,name,at,**extra):
        (output/name).write_bytes(raw)
        entry=dict(url=url,path=name,sha256=hashlib.sha256(raw).hexdigest(),fetched_at=at,**extra)
        manifest['requests'].append(entry);save();return entry
    def retained(symbol,kind):
        company=previous.get('companies',{}).get(symbol,{})
        if not isinstance(company,dict):raise ValueError('REUSE_COMPANY_METADATA_INVALID')
        entry=company.get(kind)
        if entry is None:return None
        if not isinstance(entry,dict):raise ValueError('REUSE_SOURCE_METADATA_INVALID')
        if not entry.get('path'):return None
        path=archive.safe_file(reuse,entry['path'])
        if archive.file_hash(path)!=entry['sha256']:raise ValueError('REUSE_SOURCE_HASH_MISMATCH')
        if kind in {'price','splits'}:
            function = 'SPLITS' if kind == 'splits' else 'TIME_SERIES_DAILY'
            if (entry.get('url') != 'https://www.alphavantage.co/query'
                    or entry.get('function') != function or entry.get('symbol') != symbol):
                raise ValueError('REUSE_AV_SOURCE_IDENTITY_REJECTED')
            try:
                payload = json.loads(path.read_bytes())
            except (ValueError, UnicodeError):
                payload = {'_av_error':'invalid_response'}
            status = _response_status(function, payload)
            if status != 'available':
                # Do not erase/overwrite the old immutable failure. Preserve its
                # identity/hash as a diagnostic, and allow a fresh request.
                manifest['companies'][symbol].setdefault('reuse_diagnostics', []).append(dict(
                    kind=kind, status=status, rate_limit_scope=response_diagnostic(function,payload,'demo').get('rate_limit_scope'),
                    source_sha256=entry['sha256'], action='REJECTED_REUSE_REACQUIRE'))
                return None
            if kind == 'splits':
                decode_av_splits(payload, symbol)
            else:
                decode_av_daily(payload, symbol, count=300, strict_identity=True)
        return store(path.read_bytes(),entry['url'],symbol.replace(':','_')+'-'+kind+'.json',entry['fetched_at'],
            **{k:v for k,v in entry.items() if k not in {'url','path','sha256','fetched_at'}})
    def sec_get(url,name):
        source=sec._fetch(url)
        if source:return store(source[0],url,name,source[1],owner='SEC',status='RAW_NOT_YET_QUALIFIED')
        entry=dict(url=url,status='FAILED',diagnostics=sec.source_diagnostics());manifest['requests'].append(entry);save();return entry
    native=urllib.request.urlopen
    @contextmanager
    def av_capture(symbol,entry):
        def opener(url,*args,**kwargs):
            parsed=urllib.parse.urlparse(url.full_url if hasattr(url,'full_url') else url)
            query=urllib.parse.parse_qs(parsed.query)
            if parsed.hostname!='www.alphavantage.co' or query.get('function',[None])[0] not in {'TIME_SERIES_DAILY','SPLITS'}:
                raise ValueError('UNEXPECTED_CAPTURE_REQUEST')
            ticker=symbol[:-3]
            if query.get('symbol')!=[ticker]:raise ValueError('AV_CAPTURE_SYMBOL_MISMATCH')
            try:
                with native(url,*args,**kwargs) as response:
                    if urllib.parse.urlparse(response.geturl()).hostname!='www.alphavantage.co':raise ValueError('AV_REDIRECT_REJECTED')
                    if response.status!=200:raise ValueError('AV_HTTP_STATUS_REJECTED')
                    raw=response.read();at=now()
            except Exception as exc:
                manifest['requests'].append(dict(symbol=symbol,function=query['function'][0],status='FAILED',
                    error_class=type(exc).__name__,cause_class=type(getattr(exc,'reason',None)).__name__,
                    http_status=getattr(exc,'code',None),fetched_at=now()))
                save();raise
            function=query['function'][0];mode=query.get('outputsize',[''])[0]
            saved=store(raw,'https://www.alphavantage.co/query',ticker+'-'+function+'-'+mode+'.json',at,
                symbol=symbol,function=function,mode=mode,owner='AV',status='RAW_NOT_YET_QUALIFIED')
            saved['http_status']=200
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError):
                payload = {'_av_error':'invalid_response'}
            diagnostic = response_diagnostic(function, payload, get_next_av_key(), mode=mode or None)
            if function == 'SPLITS':
                entry['splits_diagnostic'] = diagnostic
                if diagnostic['status'] == 'available':
                    # Original Owner validates listed identity, finite factors
                    # and unique event dates; HTTP 200 is never qualification.
                    try:
                        decode_av_splits(payload, symbol)
                    except (ValueError, KeyError, TypeError, ArithmeticError):
                        diagnostic.update(status='invalid_response', qualification_error='AV_SPLITS_QUALIFICATION_REJECTED')
                if diagnostic['status'] == 'available':
                    entry['splits'] = saved
                else:
                    entry['splits'] = dict(status=diagnostic['status'], diagnostics=[diagnostic])
            elif diagnostic['status'] == 'available':
                entry['price'] = saved
            class Response(io.BytesIO):
                def __enter__(self):return self
                def __exit__(self,*_):self.close()
            return Response(raw)
        urllib.request.urlopen=opener
        try:yield
        finally:urllib.request.urlopen=native
    shared_directory=None
    for symbol in symbols:
        entry=manifest['companies'][symbol]={}
        try:
            entry['directory']=retained(symbol,'directory') or shared_directory or sec_get(sec.DIRECTORY,'directory.json')
            shared_directory=entry['directory']
            directory=json.loads(archive.safe_file(output,entry['directory']['path']).read_text())
            matches=sec_ticker_matches(symbol,directory)
            if len(matches)!=1:raise ValueError('SEC_IDENTITY_UNRESOLVED')
            cik=str(int(matches[0]['cik_str'])).zfill(10)
            entry['submissions']=retained(symbol,'submissions') or sec_get('https://data.sec.gov/submissions/CIK'+cik+'.json',cik+'-submissions.json')
            sub=archive.safe_file(output,entry['submissions']['path']).read_bytes()
            issuer,scope,_=identity_projection(symbol,(json.dumps(directory).encode(),entry['directory']['fetched_at']),(sub,entry['submissions']['fetched_at']))
            entry['companyfacts']=retained(symbol,'companyfacts') or sec_get('https://data.sec.gov/api/xbrl/companyfacts/CIK'+cik+'.json',cik+'-companyfacts.json')
            entry['originals']=[]
            for original in previous.get('companies',{}).get(symbol,{}).get('originals',[]):
                path=archive.safe_file(reuse,original['path'])
                if archive.file_hash(path)!=original['sha256']:raise ValueError('REUSE_ORIGINAL_HASH_MISMATCH')
                entry['originals'].append(store(path.read_bytes(),original['url'],symbol[:-3]+'-'+Path(original['path']).name,original['fetched_at'],owner='SEC'))
            if scope['issuer_type']=='domestic' and not 6000<=int(scope['sic'])<=6999:
                entry['price']=retained(symbol,'price');entry['splits']=retained(symbol,'splits')
                if (not entry['price'] or not entry['splits'] or not entry['splits'].get('path')) and get_next_av_key()=='demo':
                    raise ValueError('AV_IDENTITY_UNCONFIGURED')
                with av_capture(symbol,entry):
                    if not entry['price']:
                        provider=AVKlineProvider();provider.get_kline(symbol,'US',count=300)
                        entry['price_diagnostic']=dict(diagnostics=getattr(provider,'diagnostics',{}),reason=provider.failure_reason)
                    if not entry['splits'] or not entry['splits'].get('path'):
                        payload = _av_get('SPLITS',symbol[:-3],get_next_av_key())
                        if 'splits_diagnostic' not in entry:
                            # Transport failures have no raw HTTP-200 body for
                            # the capture wrapper. Preserve the Owner's result.
                            diagnostic = response_diagnostic('SPLITS',payload,get_next_av_key())
                            entry['splits_diagnostic'] = diagnostic
                            entry['splits'] = dict(status=diagnostic['status'], diagnostics=[diagnostic])
            # Existing SEC Owner handles any needed Q4 original. Bounded official
            # annual-neighbor earnings events; never derive Q4 by S3 subtraction.
            if scope['issuer_type']!='domestic' or 6000<=int(scope['sic'])<=6999:
                save()
                continue
            filings=sec.discover_filings(issuer,json.loads(sub))
            existing={x['url'] for x in entry['originals']}
            annuals=[f for f in filings if f['form']=='10-K'][:1]
            candidates=[]
            if annuals:
                from datetime import date
                end=date.fromisoformat(annuals[0]['filed'])
                candidates=[f for f in filings if f['form']=='8-K' and 0<=(end-date.fromisoformat(f['filed'])).days<=7][:3]
            # CompanyFacts does not carry target-class dimensions. Reuse the
            # SEC Owner's direct listed-class parser on a bounded set of actual
            # statements, rather than guessing EPS from another listed ticker.
            listed_classes=len(issuer.get('registered_tickers',[]))>1
            if listed_classes:
                candidates += [f for f in filings if f['form']=='10-Q'][:3] + annuals
            for filing in candidates:
                if existing and not listed_classes:break  # explicit reused single-class quarter original
                if filing['full_url'] in existing:continue
                original=sec_get(filing['full_url'],symbol[:-3]+'-'+filing['accession']+'.txt')
                if original.get('path'):entry['originals'].append(original)
        except (ValueError,KeyError,TypeError,OSError,ArithmeticError) as exc:
            entry['capture_failure']=str(exc) if isinstance(exc,ValueError) and str(exc).isupper() else 'PUBLIC_SOURCE_CAPTURE_FAILED'
        save()
    manifest['finished_at']=now();save()
    return output/'manifest.json'
