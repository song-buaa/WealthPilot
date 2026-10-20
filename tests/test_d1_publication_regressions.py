"""D1 production defects on synthetic SDK/SQLite/frozen views, no live I/O."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def context(tmp_path, monkeypatch):
    from tests.test_u1_asset_views import context as original
    yield from original.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize('security_type,isin,vehicle,economic,status', [
    ('STK', None, 'ETF', 'EQUITY', 'VERIFIED'),
    ('ETF', None, 'ETF', 'EQUITY', 'VERIFIED'),
    ('OPT', None, 'OPTION', 'DERIVATIVE', 'DETERMINISTIC'),
    ('FUT', None, 'FUTURE', 'DERIVATIVE', 'DETERMINISTIC'),
    ('STK', 'US78462F1030', 'COMMON_STOCK', 'UNKNOWN', 'CONFLICT'),
    ('ETF', 'US78462F1030', 'ETF', 'UNKNOWN', 'CONFLICT'),
])
def test_tiger_snapshot_publication_and_fixed_view_preserve_classification(
        context, security_type, isin, vehicle, economic, status):
    from app.models import Position
    from app.asset_views import load_view, frozen_result
    from backend.services.asset_analysis import AssetAnalysisPreparation
    from backend.services.restricted_sync import SyncExecutor
    from services.broker_sync.models import PositionSnapshot
    from services.broker_sync.repository import PositionSnapshotRepository
    from services.broker_sync.schema import cash_position
    from services.broker_sync.tiger.adapter import TigerAdapter
    from tests.test_u0_review_regressions import stock
    c = context
    sdk = stock();sdk.contract.symbol = 'QQQ';sdk.contract.sec_type = security_type
    sdk.contract.isin = isin
    native = TigerAdapter('fixture').to_position(sdk)
    assert (native.vehicle_type,native.economic_asset_class,native.classification_verification_status) == (vehicle,economic,status)
    assert native.classification_evidence['listing_symbol'] == 'QQQ:US'
    def factory(broker):
        def sync(db,triggered_by='analysis'):
            positions = [native,cash_position('tiger','fixture','USD',50,datetime.now(timezone.utc))]
            return PositionSnapshotRepository(db).synchronize('tiger','fixture',lambda:positions,
                retries=0,triggered_by=triggered_by,metadata=lambda:dict(source='synthetic',complete=True))
        return SimpleNamespace(sync_and_persist=sync)
    executor = SyncExecutor(['tiger'],7,factory,session_factory=c.sessions,account_resolver=lambda *_:'fixture')
    service = AssetAnalysisPreparation(executor,c.db,synthetic=True)
    _,claim = service.request('publication');service.run(*claim)
    prepared = service.status('publication');assert prepared['state'] == 'ready',prepared
    with c.sessions() as db:
        snapshot = db.query(PositionSnapshot).filter_by(symbol='QQQ:US').one()
        position = db.query(Position).filter_by(symbol='QQQ:US',portfolio_id=7).one()
        assert (snapshot.vehicle_type,snapshot.economic_asset_class,snapshot.classification_verification_status) == (vehicle,economic,status)
        assert (position.vehicle_type,position.economic_asset_class,position.classification_verification_status) == (vehicle,economic,status)
        evidence = json.loads(position.classification_evidence_json)
        assert evidence['listing_symbol'] == 'QQQ:US' and evidence['isin'] == isin
        assert position.quantity == 10 and position.cost_price == 1 and position.current_price == 1
        assert position.market_value_cny == 10 and position.original_value == 10
        assert (position.broker,position.broker_account_id,position.sync_source,position.currency) == ('tiger','fixture','api','USD')
        assert db.query(Position).filter_by(portfolio_id=8).count() == 0
    view = load_view(c.db,7,prepared['snapshot_id'])
    details = frozen_result(view,'get_investment_details',{})
    item = next(i for i in details['items'] if i['symbol'] == 'QQQ:US')
    assert (item['vehicle_type'],item['economic_asset_class']) == (vehicle,economic)
    assert item['quantity'] == 10 and item['cost_price'] == 1 and item['market_value_cny'] == 10
    assert not view['issuer_exposure']['unmapped_direct_holdings']
    assert not view['issuer_exposure']['companies']  # ETF is not a direct issuer.
    stored = json.dumps(view,sort_keys=True)
    with c.sessions() as db:
        db.info['portfolio_id'] = 7
        factory('tiger').sync_and_persist(db)
        assert db.query(Position).filter_by(portfolio_id=7,symbol='QQQ:US').count() == 1
        assert db.query(Position).filter_by(symbol='QQQ:US').one().vehicle_type == vehicle
    assert json.dumps(load_view(c.db,7,prepared['snapshot_id']),sort_keys=True) == stored


def test_raw_ticker_cannot_invent_verified_listing_proof():
    from services.broker_sync.position_upsert_service import _snapshot_classification
    snap = SimpleNamespace(broker='tiger',currency='USD',name='QQQ',asset_class='etf',
        raw_data_json=json.dumps({'listing_symbol':'QQQ:US'}),classification_evidence_json='{}',
        broker_security_type='ETF',vehicle_type='ETF')
    classification,evidence = _snapshot_classification(snap)
    assert evidence.listing_symbol is None
    assert classification.economic_asset_class == 'UNKNOWN'


def test_publication_keeps_stable_id_conflict():
    from services.broker_sync.position_upsert_service import _snapshot_classification
    snap = SimpleNamespace(broker='tiger',currency='USD',name='QQQ',asset_class='etf',raw_data_json='{}',
        broker_security_type='STK',vehicle_type='ETF',classification_evidence_json=json.dumps(
            {'listing_symbol':'QQQ:US','con_id':756733,'stock_type':'COMMON'}))
    classification,evidence = _snapshot_classification(snap)
    assert evidence.con_id == 756733 and evidence.listing_symbol == 'QQQ:US'
    assert classification.economic_asset_class == 'UNKNOWN' and classification.verification_status == 'CONFLICT'


@pytest.mark.parametrize('question', ['重新评估现在的投资组合', 'Review my portfolio', '多行\n问题', ' A ', 'x'*6000])
def test_analysis_question_nonblank_content_and_scope_are_preserved(question):
    from pocs.chatgpt_skills.analysis import AnalysisRequest
    request = AnalysisRequest(question=question,analysis_scope='net_worth')
    assert request.question == question and request.analysis_scope == 'net_worth'
    schema = AnalysisRequest.model_json_schema()['properties']
    assert 'pattern' not in schema['question']
    assert schema['question']['minLength'] == 1 and schema['question']['maxLength'] == 6000
    assert schema['analysis_scope']['enum'] == ['investments','net_worth']


@pytest.mark.parametrize('payload', [
    {'question':''}, {'question':' \t\n\u3000'}, {'question':'x'*6001}, {'question':None},
    {'question':'Valid question','analysis_scope':'all'},
    {'question':'Valid question','force':True},
    {'question':'Valid question','snapshot_id':'invalid'},
])
def test_analysis_input_constraints_remain_server_enforced(payload):
    from pydantic import ValidationError
    from pocs.chatgpt_skills.analysis import AnalysisRequest
    with pytest.raises(ValidationError):AnalysisRequest.model_validate(payload)
