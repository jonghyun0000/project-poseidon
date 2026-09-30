"""Small OSM vector extracts around catalog reference points; never tile prefetch.

Run from project root: PYTHONPATH=. .venv/bin/python scripts/build_harbor_snapshots.py
This captures a limited 0.01 x 0.01 degree sector, not an entire port or chart.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from poseidon.ports.catalog import get_port

CODES = ['KRPUS', 'SGSIN', 'NLRTM', 'DEHAM', 'GBSOU', 'USLGB',
         'JPTYO', 'CNSGH', 'AUSYD', 'ESBCN', 'FRLEH', 'BRSSZ']


def main():
    output = Path('web/public/console/harbors')
    archive = Path('data/static/harbors')
    output.mkdir(exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)
    manifest = []
    with httpx.Client(timeout=30, headers={'User-Agent': 'Poseidon-local-research/1.0'}) as client:
        for code in CODES:
            target = output / (code + '.json')
            if target.exists():
                manifest.append(json.loads(target.read_text())['snapshot'])
                print(code, 'cached', flush=True)
                continue
            port = get_port('unlocode:' + code)
            if not port or port.get('lat') is None:
                print(code, 'catalog coordinate unavailable', flush=True)
                continue
            lon, lat = port['lon'], port['lat']
            bbox = ','.join(f'{n:.6f}' for n in [lon-.005, lat-.005, lon+.005, lat+.005])
            try:
                response = client.get('https://api.openstreetmap.org/api/0.6/map.json',
                                      params={'bbox': bbox})
                response.raise_for_status()
                raw = response.json()
                if 'elements' not in raw:
                    raise ValueError('missing OSM elements')
                (archive / (code + '.json')).write_bytes(response.content)
                nodes = {e['id']: e for e in raw['elements'] if e['type'] == 'node'}
                ways = []
                for e in raw['elements']:
                    tags = e.get('tags', {})
                    if e['type'] != 'way' or not (
                        tags.get('man_made') in ('pier', 'breakwater', 'quay')
                        or tags.get('building') or tags.get('waterway') == 'dock'
                    ):
                        continue
                    if any(i not in nodes for i in e['nodes']):
                        continue
                    ways.append({'type': 'way', 'id': e['id'], 'tags': tags,
                                 'geometry': [{'lat': nodes[i]['lat'], 'lon': nodes[i]['lon']}
                                              for i in e['nodes']]})
                snapshot = {'code': code, 'port_id': port['id'], 'port_name': port['name'],
                            'bbox': bbox, 'source_url': str(response.url),
                            'retrieved_at': datetime.now(timezone.utc).isoformat(),
                            'raw_sha256': hashlib.sha256(response.content).hexdigest(),
                            'features': len(ways), 'license': 'ODbL-1.0',
                            'scope': 'catalog_reference_point_sector_not_entire_port'}
                target.write_text(json.dumps({'elements': ways, 'snapshot': snapshot},
                                             ensure_ascii=False, separators=(',', ':')))
                manifest.append(snapshot)
                print(code, len(ways), 'facilities', flush=True)
            except (httpx.HTTPError, ValueError) as exc:
                print(code, type(exc).__name__, str(exc)[:120], flush=True)
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
