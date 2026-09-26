"""Offline, checksum-verified port search. Reference points are not navigation fixes."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from poseidon.core.config import settings


class CatalogUnavailable(RuntimeError):
    pass


def root() -> Path:
    return settings.data_root / 'static' / 'ports'


def fold(value: str) -> str:
    # Compose Hangul before removing combining accents from Latin names.
    value = unicodedata.normalize('NFC', value).casefold()
    return ''.join(c for c in unicodedata.normalize('NFD', value)
                   if not unicodedata.combining(c)).strip()


def compact(value: str) -> str:
    return re.sub(r'[^\w]', '', fold(value))


@lru_cache(maxsize=4)
def _load(path: str, mtime_ns: int, size: int):
    try:
        manifest = json.loads(Path(path).read_text())
        digest = manifest['catalog_sha256']
        if not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('invalid checksum')
        data = (Path(path).parent / 'catalogs' / f'{digest}.json').read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('catalog checksum mismatch')
        payload = json.loads(data)
        ports = payload['ports']
        by_id = {p['id']: p for p in ports}
        if not ports or len(by_id) != len(ports) or len(ports) != manifest['total']:
            raise ValueError('catalog count or identity mismatch')
        search = [(p, fold(' '.join([p['name'], *p['aliases'], p['country_name'],
                                   p['country_code'], p.get('unlocode') or '',
                                   p.get('wpi_id') or '', p['id']]))) for p in ports]
        return manifest, ports, by_id, search
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CatalogUnavailable('항만 목록이 없거나 무결성 검사에 실패했습니다.') from exc


def _catalog():
    path = root() / 'manifest.json'
    try:
        stat = path.stat()
    except OSError as exc:
        raise CatalogUnavailable('항만 목록을 아직 확보하지 못했습니다.') from exc
    return _load(str(path), stat.st_mtime_ns, stat.st_size)


def catalog_meta() -> dict:
    try:
        return copy.deepcopy(_catalog()[0])
    except CatalogUnavailable as exc:
        return {'schema_version': 'ports-1.0', 'status': 'unavailable',
                'message': str(exc), 'total': 0, 'located': 0,
                'countries': [], 'sources': [], 'limitations': []}


def get_port(port_id: str) -> dict | None:
    return copy.deepcopy(_catalog()[2].get(port_id))


def catalog_snapshot(port_ids) -> tuple[dict, dict]:
    manifest, _, by_id, _ = _catalog()
    return copy.deepcopy(manifest), {port_id: copy.deepcopy(by_id.get(port_id)) for port_id in port_ids}


def search_ports(q='', country=None, limit=30, offset=0) -> dict:
    manifest, _, _, search = _catalog()
    query = fold(q)
    tokens = query.split()
    code_query = compact(q)
    matches = []
    for p, haystack in search:
        if country and p['country_code'] != country.upper():
            continue
        codes = [compact(p.get('unlocode') or ''), compact(p.get('wpi_id') or ''), compact(p['id'])]
        exact = bool(code_query) and code_query in codes
        if query and not exact and not all(t in haystack for t in tokens):
            continue
        score = 0 if exact else 1 if query and any(fold(n) == query for n in [p['name'], *p['aliases']]) else 2
        matches.append((score, p))
    matches.sort(key=lambda x: (x[0], fold(x[1]['name']), x[1]['id']))
    return {'status': 'ready', 'catalog_id': manifest['catalog_id'], 'total': len(matches),
            'offset': offset, 'limit': limit,
            'items': copy.deepcopy([p for _, p in matches[offset:offset + limit]])}


def ports_geojson(country=None) -> dict:
    manifest, ports, _, _ = _catalog()
    return {'type': 'FeatureCollection', 'catalog_id': manifest['catalog_id'], 'features': [
        {'type': 'Feature', 'id': p['id'], 'geometry': {'type': 'Point', 'coordinates': [p['lon'], p['lat']]},
         'properties': {k: p[k] for k in ('id', 'name', 'country_code', 'unlocode', 'kind')}}
        for p in ports if p['lat'] is not None and (not country or p['country_code'] == country.upper())]}
