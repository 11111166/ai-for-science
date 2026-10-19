import httpx
import pytest

@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("MOLECULE_DOSSIER_CACHE_DIR",str(tmp_path))
    _cache.clear()

from app import compound_identity as module
from app.drug_catalog import Catalog, _cache


def test_cas_checksum():
    assert module.valid_cas('50-78-2')
    assert not module.valid_cas('50-78-3')
    assert not module.valid_cas('CHEMBL25')


def test_exact_structure_identity_does_not_match_similar_inchikey(monkeypatch):
    key='BSYNRYMUTXBXSQ-UHFFFAOYSA-N'
    def handle(req):
        if 'chembl' in str(req.url):return httpx.Response(200,json={'pref_name':'ASPIRIN','molecule_structures':{'standard_inchi_key':key},'molecule_properties':{'full_mwt':'180.16','full_molformula':'C9H8O4'}})
        return httpx.Response(200,json={'PropertyTable':{'Properties':[{'CID':2244,'InChIKey':key[:-1]+'O'}]}})
    monkeypatch.setattr(module,'Catalog',lambda:Catalog(httpx.Client(transport=httpx.MockTransport(handle))))
    _cache.clear();r=module.identity('CHEMBL25')
    assert r['pubchem_cid'] is None and r['cas_numbers']==[] and r['warnings']
    _cache.clear()


def test_reference_aspirin_has_verified_primary_cas(monkeypatch):
    key='BSYNRYMUTXBXSQ-UHFFFAOYSA-N'
    def handle(req):
        if 'chembl' in str(req.url):return httpx.Response(200,json={'pref_name':'ASPIRIN','molecule_structures':{'standard_inchi_key':key},'molecule_properties':{'full_mwt':'180.16','full_molformula':'C9H8O4'}})
        if '/property/' in str(req.url):return httpx.Response(200,json={'PropertyTable':{'Properties':[{'CID':2244,'InChIKey':key}]}})
        return httpx.Response(200,json={'InformationList':{'Information':[{'CID':2244,'Synonym':['50-78-2','11126-35-5','50-78-3']}]}})
    monkeypatch.setattr(module,'Catalog',lambda:Catalog(httpx.Client(transport=httpx.MockTransport(handle))))
    _cache.clear();r=module.identity('CHEMBL25')
    assert r['pubchem_cid']==2244 and r['cas_numbers']==['50-78-2']
    assert r['molecular_formula']=='C9H8O4' and r['molecular_weight']==180.16
    assert r['cas_source_label']=='NIST Chemistry WebBook'
    _cache.clear()
