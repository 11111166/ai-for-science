"""Same-origin SVG structure images with bounded persistent server cache."""
from pathlib import Path
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from .auth import get_current_user

router = APIRouter()
_slots = threading.BoundedSemaphore(2)
def safe_svg(data):
    if len(data)>MAX_BYTES or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper(): raise ValueError('Invalid SVG')
    root=ET.fromstring(data)
    if root.tag!='{http://www.w3.org/2000/svg}svg': raise ValueError('Not SVG')
    allowed={'svg','g','path','rect','circle','ellipse','line','polyline','polygon','text','tspan'}
    for node in root.iter():
        if node.tag.split('}')[-1] not in allowed: raise ValueError('Active SVG element')
        for key,value in node.attrib.items():
            if key.split('}')[-1].lower().startswith('on') or key.split('}')[-1] in {'href','src'} or re.search(r'url\s*\(|@import|javascript:|expression\s*\(',value,re.I):
                raise ValueError('External or active SVG attribute')
    return ET.tostring(root,encoding='utf-8')
MAX_BYTES = 2 * 1024 * 1024

def structure(identifier: str):
    identifier = identifier.upper()
    if not re.fullmatch(r'CHEMBL\d{1,12}', identifier):
        raise HTTPException(422, 'Invalid molecule identifier')
    root = Path(os.environ.get('MOLECULE_STRUCTURE_CACHE_DIR', './data/shared/cache/molecule-structures'))
    path = root / f'{identifier}.svg'
    old = None
    try:
        old = path.read_bytes()
        old = safe_svg(old)
        if old and time.time() - path.stat().st_mtime < 30 * 86400:
            return old, 'cached'
    except (OSError, ValueError, ET.ParseError):
        old = None
    if not _slots.acquire(timeout=10):
        if old: return old, 'stale'
        raise HTTPException(503, '结构查询繁忙，请稍后重试')
    try:
        # Fixed host and validated identifier; never accept arbitrary URLs.
        with httpx.Client(timeout=12, follow_redirects=False) as client:
            with client.stream('GET', f'https://www.ebi.ac.uk/chembl/api/data/image/{identifier}.svg?dimensions=800') as response:
                response.raise_for_status()
                parts=[]; size=0
                for part in response.iter_bytes():
                    size += len(part)
                    if size > MAX_BYTES: raise ValueError('Image too large')
                    parts.append(part)
                data=b''.join(parts)
                data = safe_svg(data)
        try:
            root.mkdir(parents=True, exist_ok=True)
            temporary=path.with_suffix(f'.{threading.get_ident()}.tmp')
            temporary.write_bytes(data); temporary.replace(path)
        except OSError:
            pass
        return data, 'fresh'
    except (httpx.HTTPError, ValueError, ET.ParseError):
        if old: return old, 'stale'
        raise HTTPException(503, '结构图暂未取回，请稍后重试')
    finally:
        _slots.release()

@router.get('/molecules/{identifier}/structure')
def molecule_structure(identifier: str, user=Depends(get_current_user)):
    data, state = structure(identifier)
    return Response(data, media_type='image/svg+xml', headers={'Cache-Control':'private, max-age=86400', 'X-Structure-Cache':state, 'X-Content-Type-Options':'nosniff', 'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; sandbox"})
