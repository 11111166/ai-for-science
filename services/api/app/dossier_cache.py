"""Bounded public-data cache: persistent last-good data, background refresh."""
from collections import OrderedDict
from pathlib import Path
from threading import Lock, BoundedSemaphore, Thread
import hashlib
import json
import os
import time

_cache = OrderedDict()
_lock = Lock()
_slots = BoundedSemaphore(2)
_refreshing = set()


def _path(key):
    root = Path(os.environ.get('MOLECULE_DOSSIER_CACHE_DIR', './data/shared/cache/molecule-dossiers'))
    return root / (hashlib.sha256(json.dumps(key).encode()).hexdigest() + '.json')


def _remember(key, entry):
    with _lock:
        _cache[key] = entry
        _cache.move_to_end(key)
        while len(_cache) > 128:
            _cache.popitem(last=False)


def _read(key):
    with _lock:
        entry = _cache.get(key)
    if entry:
        return entry
    try:
        from .persistent_cache import get_json_artifact
        persisted = get_json_artifact("molecule_dossier", key)
        if persisted:
            _remember(key, persisted)
            return persisted
    except Exception:
        pass
    try:
        path = _path(key)
        if path.stat().st_size > 2_000_000:
            return None
        saved = json.loads(path.read_text())
        if saved['data']['status'] not in ('available', 'partial'):
            return None
        entry = (float(saved['expires']), saved['data'])
        _remember(key, entry)
        return entry
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _store(key, result):
    ttl = 86400 if result['status'] == 'available' else 300 if result['status'] == 'partial' else 15
    entry = (time.time() + ttl, result)
    _remember(key, entry)
    if result['status'] not in ('available', 'partial'):
        return
    try:
        from .persistent_cache import put_json_artifact
        put_json_artifact(
            "molecule_dossier",
            key,
            result,
            ttl_seconds=ttl,
            source="public_molecule_sources",
            source_version="current",
            operation=str(key[0]) if isinstance(key, tuple) and key else "lookup",
        )
    except Exception:
        pass
    try:
        path = _path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'expires': entry[0], 'data': result}, ensure_ascii=False))
        temporary.replace(path)
        files = sorted(path.parent.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[256:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass  # Read-only deployments retain the in-memory cache.


def cached(key, build, force=False):
    old = _read(key)
    if old and old[0] > time.time() and not force:
        return {**old[1], 'cache_state': old[1].get('cache_state', 'cached')}
    usable = old and old[1].get('status') in ('available', 'partial')

    def refresh():
        try:
            try:
                result = build()
            except Exception:
                result = {'status': 'unavailable', 'warnings': ['资料暂未取回，请重试。']}
            # Never overwrite a complete dossier with a partial response or an outage.
            if usable and (result['status'] == 'unavailable' or (old[1]['status'] == 'available' and result['status'] != 'available')):
                _remember(key, (time.time() + 60, {**old[1], 'cache_state': 'stale', 'refresh_failed': True}))
            else:
                _store(key, result)
            return result
        finally:
            with _lock:
                _refreshing.discard(key)
            _slots.release()

    with _lock:
        can_start = key not in _refreshing and _slots.acquire(blocking=False)
        if can_start:
            _refreshing.add(key)
    if force and can_start:
        result = refresh()
        if usable and (result['status'] == 'unavailable' or (old[1]['status'] == 'available' and result['status'] != 'available')):
            return {**old[1], 'cache_state': 'refresh_failed', 'refresh_failed': True}
        return {**result, 'cache_state': 'fresh'}
    if usable:
        if can_start:
            Thread(target=refresh, daemon=True).start()
        return {**old[1], 'cache_state': 'stale'}
    if not can_start:
        return {'status': 'unavailable', 'items': [], 'warnings': ['资料查询繁忙，请稍后重试。']}
    return {**refresh(), 'cache_state': 'fresh'}
