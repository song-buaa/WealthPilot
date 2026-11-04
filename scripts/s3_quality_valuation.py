"""Explicit offline internal S3 run/read/replay/diff entry; no Web/MCP writes."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--request", type=Path, required=True)
    run.add_argument("--p1-root", type=Path, required=True)
    run.add_argument("--archive", type=Path, required=True)
    run.add_argument("--method-applicability", action="store_true")
    run.add_argument("--official-root", type=Path)
    pool = sub.add_parser('pool', help='Explicit real pool capture/qualification/assumptions/compare/freeze')
    pool.add_argument('--symbols', nargs='+', required=True)
    pool.add_argument('--purpose', required=True)
    pool.add_argument('--pool-id', default='user_research_pool')
    pool.add_argument('--pool-version', default='v1')
    pool.add_argument('--sources', type=Path)
    pool.add_argument('--capture-root', type=Path)
    pool.add_argument('--reuse-sources', type=Path)
    pool.add_argument('--av-env-file', type=Path)
    pool.add_argument('--assumptions', type=Path)
    pool.add_argument('--archive', type=Path, required=True)
    operating = sub.add_parser('operating', help='P2 same-frozen-input operating scenarios; explicit research dossier')
    operating.add_argument('parent', type=Path)
    operating.add_argument('--dossier',type=Path,required=True)
    operating.add_argument('--archive',type=Path,required=True)
    recalc = sub.add_parser('recalculate', help='Change explicit assumptions on an existing frozen decision clock')
    recalc.add_argument('bundle', type=Path)
    recalc.add_argument('--assumptions', type=Path, required=True)
    recalc.add_argument('--archive', type=Path, required=True)
    recalc.add_argument('--pool-version', required=True)
    for action in ("read", "replay"):
        item = sub.add_parser(action)
        item.add_argument("bundle", type=Path)
    diff = sub.add_parser("diff")
    diff.add_argument("old", type=Path)
    diff.add_argument("new", type=Path)
    args = parser.parse_args()
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    if args.command == 'pool':
        from datetime import datetime, timezone
        from backend.services.s3_pool_research import make_bundle as pool_bundle
        from backend.services.s3_pool_research import PoolRequest
        from backend.services.quality_valuation_research import file_hash
        from backend.utils.symbol import parse_symbol, normalize_symbol
        symbols = []
        for value in args.symbols:
            try:ticker,market = parse_symbol(value.upper())
            except ValueError:
                if ':' in value:raise
                ticker,market = value.upper(),'US'
            symbols.append(normalize_symbol(ticker,market))
        if len(set(symbols))!=len(symbols):parser.error('Duplicate pool security.')
        PoolRequest.model_validate(dict(pool_id=args.pool_id,pool_version=args.pool_version,
            purpose=args.purpose,decision_at=datetime.now(timezone.utc),
            source_manifest_sha256='0'*64,companies={s:None for s in symbols}))
        if bool(args.sources) == bool(args.capture_root):
            parser.error('Choose --sources OR --capture-root; collection is explicit.')
        if args.capture_root:
            from backend.services.s3_pool_capture import capture_pool
            capture_pool(symbols,args.capture_root,reuse=args.reuse_sources,av_env_file=args.av_env_file)
        elif args.reuse_sources or args.av_env_file:
            parser.error('Capture options require --capture-root')
        source_root = args.sources or args.capture_root
        request = dict(pool_id=args.pool_id,pool_version=args.pool_version,purpose=args.purpose,
            decision_at=datetime.now(timezone.utc).isoformat(),
            source_manifest_sha256=file_hash(source_root/'manifest.json'),companies={s:None for s in symbols})
        if args.assumptions:
            supplied = json.loads(args.assumptions.read_text())
            if set(supplied.get('companies',{})) != set(symbols):
                parser.error('Assumptions must name exactly the requested pool.')
            request.update(companies=supplied['companies'],assumption_context=supplied.get('assumption_context',{}))
    # Block socket access before any business import. Inputs must already exist.
    import socket
    def deny(*_args, **_kwargs):
        raise RuntimeError("S3_OFFLINE_NETWORK_FORBIDDEN")
    socket.socket.connect = deny
    socket.create_connection = deny
    from backend.services.quality_valuation_research import make_bundle, save_bundle, read_bundle, replay, difference
    if args.command == 'operating':
        from backend.services.s3_operating_research import make_bundle as operating_bundle
        bundle,files=operating_bundle(args.parent,json.loads(args.dossier.read_text()))
        target=save_bundle(args.archive,bundle,files)
        result=dict(artifact_id=bundle['artifact_id'],bundle=str(target/'bundle.json'),
            comparable_count=bundle['payload']['result']['comparable_count'])
    elif args.command == 'pool':
        bundle, files = pool_bundle(source_root, request)
        path = save_bundle(args.archive,bundle,files)
        result = dict(artifact_id=bundle['artifact_id'],bundle=str(path/'bundle.json'),
            report=str(path/'comparison.md'),status=bundle['payload']['result']['status'],
            comparable_count=bundle['payload']['result']['comparable_count'])
    elif args.command == 'recalculate':
        from backend.services.s3_pool_research import make_bundle as pool_bundle, replay as pool_replay
        pool_replay(args.bundle)
        old = read_bundle(args.bundle)['payload']
        request = dict(old['request'],pool_version=args.pool_version)
        supplied = json.loads(args.assumptions.read_text())
        if set(supplied.get('companies',{})) != set(request['companies']) or 'assumption_context' not in supplied:
            parser.error('Override needs exact pool and explicit assumption_context.')
        request.update(companies=supplied['companies'],assumption_context=supplied['assumption_context'])
        bundle,files = pool_bundle(args.bundle.parent/'sources'/'inputs',request)
        path = save_bundle(args.archive,bundle,files)
        result = dict(artifact_id=bundle['artifact_id'],bundle=str(path/'bundle.json'),
            comparable_count=bundle['payload']['result']['comparable_count'])
    elif args.command == "run":
        if args.method_applicability:
            from backend.services.s3_method_research import make_bundle as method_bundle
            bundle, files = method_bundle(args.p1_root, json.loads(args.request.read_text()), args.official_root)
        else:
            if args.official_root:
                parser.error('--official-root requires --method-applicability')
            bundle, files = make_bundle(args.p1_root, json.loads(args.request.read_text()))
        path = save_bundle(args.archive, bundle, files)
        result = dict(artifact_id=bundle["artifact_id"], bundle=str(path / "bundle.json"),
                      report=str(path / "comparison.md"), status=bundle["payload"]["result"]["status"])
    elif args.command == "read":
        result = read_bundle(args.bundle)["payload"]["result"]
    elif args.command == "replay":
        if read_bundle(args.bundle)['payload']['result']['method_version'].startswith('s3-p2.'):
            from backend.services.s3_operating_research import replay as operating_replay
            result=operating_replay(args.bundle)
        elif read_bundle(args.bundle)['payload']['result']['method_version'].startswith('s3-d.'):
            from backend.services.s3_pool_research import replay as pool_replay
            result = pool_replay(args.bundle)
        elif read_bundle(args.bundle)['payload']['result']['method_version'].startswith('s3-c.'):
            from backend.services.s3_method_research import replay as method_replay
            result = method_replay(args.bundle)
        else:
            result = replay(args.bundle)
    else:
        result = difference(args.old, args.new)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
