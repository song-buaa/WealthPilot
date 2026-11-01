"""Thin adapter boundaries; synthetic rule cases are not real-company acceptance."""
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from backend.services.s3_research_entry import CompanyPoolRequest, private_archive, run_company_pool, read_company_pool


@pytest.mark.parametrize('payload', [
    {'symbols': [], 'purpose': 'research'},
    {'symbols': ['AAPL', 'aapl:us'], 'purpose': 'research'},
    {'symbols': ['0700:HK'], 'purpose': 'research'},
    {'symbols': ['AAPL'], 'purpose': ' '},
    {'symbols': ['AAPL'], 'purpose': 'research', 'decision_at': '2020-01-01'},
    {'symbols': ['AAPL'], 'purpose': 'research', 'source_root': '/tmp'},
    {'symbols': ['AAPL'], 'purpose': 'research', 'financial_facts': {'eps': 100}},
    {'symbols': ['AAPL'], 'purpose': 'research', 'order': {}},
])
def test_no_client_sources_clocks_facts_or_trading(payload):
    with pytest.raises(ValidationError):
        CompanyPoolRequest.model_validate(payload)


def test_private_root_and_symlink_refusal(tmp_path):
    root = private_archive(tmp_path/'private')
    assert root.stat().st_mode & 0o777 == 0o700
    link = tmp_path/'link'; link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match='S3_ARCHIVE_INVALID'):
        private_archive(link/'nested')
    wide = tmp_path/'wide'; wide.mkdir(mode=0o755); wide.chmod(0o755)
    with pytest.raises(ValueError, match='S3_ARCHIVE_INVALID'):
        private_archive(wide)


def failed_sources(root, symbols=('AAA:US', 'BBB:US')):
    root.mkdir()
    (root/'manifest.json').write_text(json.dumps({'schema_version':'s3-d.sources.v1',
        'companies':{s:{'directory':{'status':'FAILED','diagnostics':[{'category':'http_403'}]}} for s in symbols},
        'requests':[]}))
    return root


def test_partial_pool_is_preserved_frozen_and_read_without_capture(tmp_path, monkeypatch):
    from backend.services import s3_pool_capture
    def denied(*a, **k): raise AssertionError('read unexpectedly captured sources')
    monkeypatch.setattr(s3_pool_capture, 'capture_pool', denied)
    source = failed_sources(tmp_path/'sources')
    result = run_company_pool({'symbols':['BBB','AAA'], 'purpose':'synthetic missing-source boundary'},
        archive_root=tmp_path/'archive', source_root=source, synthetic=True)
    assert result['data_quality']['comparable_count'] == 0
    assert '| 不参与排名 |' in result['conclusion']
    assert '| 稳定 |' not in result['conclusion']
    assert len(result['professional_result']['cards']) == 2
    assert all(c['rank'] is None for c in result['professional_result']['cards'])
    artifact = result['evidence']['artifact_id']
    reread = read_company_pool(artifact, archive_root=tmp_path/'archive', synthetic=True)
    assert result == reread
    assert reread['evidence']['verification']['verified']
    assert not reread['professional_analysis_complete'] and not reread['execution_authorized']
    path = tmp_path/'archive'/artifact/'comparison.md'
    path.write_text('tampered')
    with pytest.raises(ValueError, match='ARCHIVED_VIEW_HASH_MISMATCH'):
        read_company_pool(artifact, archive_root=tmp_path/'archive')


def test_method_drift_keeps_original_result_requires_original_replay(tmp_path, monkeypatch):
    from backend.services import s3_pool_research
    r = run_company_pool({'symbols':['AAA'], 'purpose':'synthetic drift'}, archive_root=tmp_path/'archive',
        source_root=failed_sources(tmp_path/'sources', ('AAA:US',)), synthetic=True)
    def drift(*a): raise ValueError('METHOD_DRIFT_REPLAY_REQUIRES_ORIGINAL_VERSION')
    monkeypatch.setattr(s3_pool_research, 'replay', drift)
    read = read_company_pool(r['evidence']['artifact_id'], archive_root=tmp_path/'archive', synthetic=True)
    assert read['professional_result'] == r['professional_result']
    assert not read['evidence']['verification']['verified']
    assert read['evidence']['verification']['integrity_verified']


def test_artifact_path_not_client_controlled(tmp_path):
    with pytest.raises(ValueError, match='S3_ARTIFACT_INVALID'):
        read_company_pool('../../outside', archive_root=tmp_path/'archive')


def test_large_evidence_projection_retains_every_decision_and_resolves_citations():
    from copy import deepcopy
    from backend.services.s3_research_entry import transport_result
    from tests.test_s3_d_real_pool import pool
    from backend.services.s3_pool_research import compare_pool
    raw, assessments, prices = pool()
    result, _ = compare_pool(raw, assessments, prices)
    original = deepcopy(result)
    # Repeated uncited Owner audit trees exceed the original receipt limit.
    for card in result['cards']:
        card['quality']['financial_inputs']['synthetic_large_audit_tree'] = 'x' * (2*1024*1024)
    projected = transport_result(result)
    assert len(json.dumps(projected).encode()) < 2*1024*1024
    for before, after in zip(original['cards'], projected['cards']):
        assert before['group'] == after['group'] and before['rank'] == after['rank']
        assert before['scenarios'] == after['scenarios'] and before['sensitivity'] == after['sensitivity']
        assert before['quality']['status'] == after['quality']['status']
        assert before['quality']['priority_blockers'] == after['quality']['priority_blockers']
        for name, floor in after['quality']['floors'].items():
            assert floor['status'] == before['quality']['floors'][name]['status']
            for ref in floor.get('evidence_refs', []):
                assert ref in projected['evidence_catalog']
    assert result['cards'][0]['quality']['financial_inputs']['synthetic_large_audit_tree']


def test_synthetic_provenance_cannot_upgrade_across_reader_modes(tmp_path):
    r = run_company_pool({'symbols':['AAA'], 'purpose':'synthetic provenance boundary'},
        archive_root=tmp_path/'archive', source_root=failed_sources(tmp_path/'sources', ('AAA:US',)), synthetic=True)
    reread = read_company_pool(r['evidence']['artifact_id'], archive_root=tmp_path/'archive', synthetic=False)
    assert reread['synthetic'] is True and reread == r


def test_unknown_artifact_read_does_not_create_archive(tmp_path):
    path = tmp_path/'missing'
    with pytest.raises(OSError):
        read_company_pool('a'*64, archive_root=path)
    assert not path.exists()
