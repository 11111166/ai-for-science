import httpx
import pytest
import app.drug_catalog as drug_catalog
from app.drug_catalog import Catalog, cached, _cache


@pytest.fixture(autouse=True)
def no_external_sources(monkeypatch):
    monkeypatch.setattr(drug_catalog, '_pubchem_assays', lambda symbol: None)
    monkeypatch.setattr(drug_catalog, '_clinical_drugs', lambda ensembl_id: None)
    monkeypatch.setattr(drug_catalog, '_snapshot_items', lambda symbol: [])


def test_dossier_preserves_indication_specific_stage_and_partial_failure():
    def handle(req):
        if 'drug_indication' in req.url.path:
            return httpx.Response(200,json={'drug_indications':[{'efo_id':'EFO_1','max_phase_for_ind':'2.0','indication_refs':[{'ref_id':'NCT1'}]}],'page_meta':{'total_count':51}})
        if 'mechanism' in req.url.path:
            return httpx.Response(503)
        return httpx.Response(200,json={'pref_name':'TEST','max_phase':4,'molecule_synonyms':[{'molecule_synonym':'Alias'}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result=Catalog(client).dossier('CHEMBL1')
    assert result['status']=='partial'
    assert result['max_phase']==4 and result['indications'][0]['max_phase_for_ind']==2
    assert result['indication_total']==51 and result['aliases']==['Alias']
    assert result['mechanisms']==[] and result['warnings']


def test_target_separates_mechanism_from_activity_without_invented_direction():
    def handle(req):
        path=req.url.path
        if 'target/search' in path:
            return httpx.Response(200,json={'targets':[{'target_chembl_id':'CHEMBL10','organism':'Homo sapiens','target_type':'SINGLE PROTEIN','target_components':[{'target_component_synonyms':[{'syn_type':'GENE_SYMBOL','component_synonym':'ACE'}]}]}]})
        if 'mechanism' in path:
            return httpx.Response(200,json={'mechanisms':[{'molecule_chembl_id':'CHEMBL1','action_type':'INHIBITOR'}]})
        if 'activity' in path:
            return httpx.Response(200,json={'activities':[{'molecule_chembl_id':'CHEMBL2','standard_type':'Ki','standard_value':'0','standard_units':'nM'}]})
        return httpx.Response(200,json={'molecules':[{'molecule_chembl_id':'CHEMBL1','max_phase':4},{'molecule_chembl_id':'CHEMBL2','max_phase':None,'molecule_properties':{'full_mwt':'123.4','alogp':'0'}}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result=Catalog(client).target('ACE')
    first,second=result['items']
    assert first['mechanisms'] and not first['activities']
    assert second['activities'][0]['standard_value']=='0' and not second['mechanisms']
    assert second['max_phase'] is None
    assert second['descriptors']['molecular_weight']==123.4 and second['descriptors']['logp']==0
    assert second['descriptors']['qed'] is None


def test_external_error_is_distinct_from_no_mapping_and_cached_results_are_bounded():
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(502))) as client:
        assert Catalog(client).target('ACE')['status']=='unavailable'
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'targets':[]}))) as client:
        result=Catalog(client).target('ACE')
        assert result['status']=='partial' and result['warnings'] and not result['items']
    _cache.clear()
    for i in range(140):cached(('test',i),lambda:{'status':'available','items':[]})
    assert len(_cache)==128
    _cache.clear()


def test_target_retains_independent_sources_during_chembl_outage(monkeypatch):
    monkeypatch.setattr(drug_catalog, '_pubchem_assays', lambda symbol: [{
        'identifier': 'CID:4670', 'name': 'measured inhibitor',
        'assays': [{'aid': 414229, 'result_type': 'Inhibition', 'reported_value': 92}],
    }])
    monkeypatch.setattr(drug_catalog, '_clinical_drugs', lambda ensembl_id: [])
    monkeypatch.setattr(drug_catalog, '_snapshot_items', lambda symbol: [{
        'identifier': 'CHEMBL42', 'name': 'archived molecule', 'max_phase': 0,
        'mechanisms': [], 'activities': [], 'activity_count': 2,
        'evidence_source': 'ChEMBL 固定快照',
    }])
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(502))) as client:
        result = Catalog(client).target('PLA2G6', 'ENSG00000184381')
    assert result['status'] == 'partial'
    assert result['source_status']['chembl'] == 'unavailable'
    assert result['items'][0]['identifier'] == 'CHEMBL42'
    assert result['pubchem_items'][0]['identifier'] == 'CID:4670'
    assert result['clinical_items'] == []


def test_comparison_activity_records_keep_conditions_and_censoring():
    def handle(req):
        assert req.url.params['molecule_chembl_id']=='CHEMBL1'
        assert req.url.params['target_chembl_id']=='CHEMBL10'
        assert req.url.params['limit']=='100'
        return httpx.Response(200,json={'activities':[{'standard_type':'IC50','standard_value':'100','standard_relation':'<','standard_units':'nM','assay_chembl_id':'CHEMBL20','assay_description':'binding','target_organism':'Homo sapiens'}], 'page_meta':{'total_count':120}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result=Catalog(client).activities('CHEMBL1','CHEMBL10')
    assert result['total']==120 and result['items'][0]['standard_relation']=='<'
    assert result['items'][0]['assay_description']=='binding'
