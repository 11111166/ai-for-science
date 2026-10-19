import pytest
from fastapi import HTTPException
from app import molecule_structure as m

def test_cached_svg_needs_no_external_network(tmp_path,monkeypatch):
    monkeypatch.setenv('MOLECULE_STRUCTURE_CACHE_DIR',str(tmp_path))
    data=m.safe_svg(b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>');(tmp_path/'CHEMBL25.svg').write_bytes(data)
    monkeypatch.setattr(m.httpx,'Client',lambda **k:pytest.fail('must not use network'))
    assert m.structure('chembl25')==(data,'cached')

def test_invalid_identifiers_cannot_be_urls():
    with pytest.raises(HTTPException) as error:m.structure('../../secret')
    assert error.value.status_code==422

def test_stale_image_survives_external_outage(tmp_path,monkeypatch):
    import os
    monkeypatch.setenv('MOLECULE_STRUCTURE_CACHE_DIR',str(tmp_path))
    data=m.safe_svg(b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>');p=tmp_path/'CHEMBL25.svg';p.write_bytes(data);os.utime(p,(1,1))
    def fail(**kwargs):raise m.httpx.ConnectError('unreachable')
    monkeypatch.setattr(m.httpx,'Client',fail)
    assert m.structure('CHEMBL25')==(data,'stale')

@pytest.mark.parametrize('data',[b'<html/>',b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',b'<svg xmlns="http://www.w3.org/2000/svg"><path style="fill:url(https://bad)"/></svg>'])
def test_rejects_active_or_external_svg(data):
    with pytest.raises(ValueError):m.safe_svg(data)
