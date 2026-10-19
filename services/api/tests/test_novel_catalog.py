import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.novel_catalog import CatalogNovelAdapter, SCHEMA, score_association, novel_adapter, novel_availability
from app.open_targets import OpenTargetsError


def row(identifier='ENSG1', **scores):
    return {'target':{'id':identifier,'approvedSymbol':'GENE','approvedName':'Test gene','biotype':'protein_coding'},
            'datatypeScores':[{'id':k,'score':v} for k,v in scores.items()], 'datasourceScores':[{'id':'eva','score':.5}]}


def make_catalog(path):
    path.mkdir(exist_ok=True)
    targets=[score_association(row(genetic_association=.8, clinical=.9, literature=.9))]
    encoded=gzip.compress(json.dumps({'disease_id':'MONDO_TEST','targets':targets}).encode())
    (path/'disease.json.gz').write_bytes(encoded)
    disease={'id':'MONDO_TEST','name':'Test disease','name_zh':'测试疾病','aliases':['Test alias'], 'file':'disease.json.gz',
             'sha256':hashlib.sha256(encoded).hexdigest(),'candidate_count':1,'association_count':10}
    (path/'manifest.json').write_text(json.dumps({'schema_version':SCHEMA,'quality_passed':True,'source_version':'26.06',
                                               'retrieved_at':'2026-09-10T00:00:00+00:00','diseases':[disease]}))


def test_drug_and_literature_cannot_generate_or_promote_candidates():
    assert score_association(row(clinical=1, literature=1)) is None
    low=score_association(row(genetic_association=.8))
    high=score_association(row(genetic_association=.8,clinical=1,literature=1))
    assert low['exploration_score']==high['exploration_score']==.36
    assert not low['known_drug'] and high['known_drug']
    assert score_association(row(genetic_association=.8,genetic_literature=.8))['exploration_score']==.36
    with pytest.raises(ValueError):score_association(row(genetic_association=2))


def test_catalog_materializes_traceable_evidence_without_fake_model_outputs(tmp_path):
    make_catalog(tmp_path)
    adapter=CatalogNovelAdapter(tmp_path)
    for query in ['MONDO_TEST','Test disease','测试疾病','Test alias']:
        assert adapter.availability(query)['available']
    result=adapter.discover('测试疾病',100)
    assert result['parameters']['discovery_strategy']=='novel'
    assert result['parameters']['include_indirect'] is False
    assert result['benchmark']['status']=='not_validated'
    assert result['data_quality']['analysis_tier']=='public_evidence_exploration'
    target=result['targets'][0]
    assert target['direction']=='not_available'
    assert target['causal_confidence'] is None
    assert target['development_feasibility'] is None
    assert 'single_cell_context' not in target['evidence_breakdown']
    assert len(result['evidence'])==1
    assert result['evidence'][0]['details']['aggregation_level']=='datatype'
    assert target['evidence_breakdown']['held_out']['known_drug_label'] is True
    with pytest.raises(OpenTargetsError):adapter.discover('Another disease')
    (tmp_path/'disease.json.gz').write_bytes(b'corrupt')
    with pytest.raises(OpenTargetsError):adapter.discover('MONDO_TEST')


def test_missing_specialist_pack_uses_explicit_catalog_tier(tmp_path):
    make_catalog(tmp_path)
    settings=SimpleNamespace(novel_target_discovery_snapshot_path='',novel_target_catalog_path=str(tmp_path))
    assert novel_availability(settings,'测试疾病')['analysis_tier']=='public_evidence_exploration'
    assert isinstance(novel_adapter(settings,'测试疾病'),CatalogNovelAdapter)
    assert novel_availability(settings,'Unknown')['available'] is False
    broken=tmp_path/'broken.json';broken.write_text('broken')
    settings.novel_target_discovery_snapshot_path=str(broken)
    assert novel_availability(settings,'测试疾病')['available'] is False
    with pytest.raises(OpenTargetsError):novel_adapter(settings,'测试疾病')


def test_empty_scans_complete_honestly_and_subtype_scope_is_not_called_direct(tmp_path):
    make_catalog(tmp_path)
    file=tmp_path/'manifest.json';manifest=json.loads(file.read_text())
    manifest['diseases'][0]['association_scope']='including_descendants'
    file.write_text(json.dumps(manifest))
    result=CatalogNovelAdapter(tmp_path).discover('MONDO_TEST')
    assert result['parameters']['include_indirect'] is True
    assert '子类型' in result['targets'][0]['evidence_breakdown']['exploration']['summary']
    assert '子类型' in result['data_quality']['limitations'][0]
    encoded=gzip.compress(json.dumps({'disease_id':'MONDO_TEST','targets':[]}).encode())
    (tmp_path/'disease.json.gz').write_bytes(encoded)
    manifest['diseases'][0].update(sha256=hashlib.sha256(encoded).hexdigest(),candidate_count=0)
    file.write_text(json.dumps(manifest))
    adapter=CatalogNovelAdapter(tmp_path)
    assert adapter.availability('MONDO_TEST')['available'] is True
    result=adapter.discover('MONDO_TEST')
    assert result['targets']==[]
    assert '没有满足' in result['data_quality']['conclusion']
