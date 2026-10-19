import json
import time
from threading import Event
from app import dossier_cache as cache


def test_persistent_cache_survives_restart_and_failed_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv('MOLECULE_DOSSIER_CACHE_DIR', str(tmp_path))
    cache._cache.clear()
    key=('molecule','CHEMBL900')
    cache.cached(key, lambda: {'status':'available','name':'saved','retrieved_at':'original'})
    cache._cache.clear()
    assert cache.cached(key, lambda: (_ for _ in ()).throw(AssertionError()))['name']=='saved'
    path=cache._path(key)
    data=json.loads(path.read_text());data['expires']=0;path.write_text(json.dumps(data));cache._cache.clear()
    done=Event()
    def failure():
        done.set()
        return {'status':'unavailable'}
    stale=cache.cached(key,failure)
    assert stale['cache_state']=='stale' and stale['retrieved_at']=='original'
    assert done.wait(2)
    for _ in range(100):
        if key not in cache._refreshing:break
        time.sleep(.01)
    assert json.loads(path.read_text())['data']['name']=='saved'
    assert cache.cached(key,failure)['name']=='saved'
    cache._cache.clear()


def test_partial_result_persists_and_empty_is_not_failure(tmp_path,monkeypatch):
    monkeypatch.setenv('MOLECULE_DOSSIER_CACHE_DIR',str(tmp_path));cache._cache.clear()
    key=('molecule','CHEMBL901')
    cache.cached(key,lambda:{'status':'partial','name':'known','indications':[]})
    cache._cache.clear()
    assert cache.cached(key,lambda:None)['name']=='known'
    cache._cache.clear()


def test_force_refresh_replaces_unexpired_partial_result(tmp_path, monkeypatch):
    monkeypatch.setenv('MOLECULE_DOSSIER_CACHE_DIR', str(tmp_path))
    cache._cache.clear()
    key = ('target', ('PLA2G6', 'ENSG00000184381'))
    cache.cached(key, lambda: {'status': 'partial', 'items': [], 'warnings': ['source unavailable']})
    calls = []
    def updated():
        calls.append(True)
        return {'status': 'available', 'items': [{'identifier': 'CHEMBL1'}], 'warnings': []}
    result = cache.cached(key, updated, force=True)
    assert calls and result['status'] == 'available' and len(result['items']) == 1
    assert cache.cached(key, lambda: None)['status'] == 'available'
    cache._cache.clear()
