"""Acquire primary-source snapshots and atomically publish an offline port catalog.

Usage: python -m poseidon.ports.build --download (refresh sources), or --offline
(rebuild the pinned raw acquisition). This does not schedule any external work.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlencode
import zipfile

from geographiclib.geodesic import Geodesic
from poseidon.ports.catalog import root, fold

UN_URL = 'https://opensource.unicc.org/un/unece/uncefact/vocab-locode/-/jobs/artifacts/2025-1/download?job=package-release'
WPI_LAYER = 'https://fgmod.nga.mil/nauticalpubs-feature/rest/services/WPI/WPI_Viewer/FeatureServer/0'
SOURCE_INFO = {
    'unlocode': {'id': 'unlocode', 'title': 'UNECE UN/LOCODE', 'url': 'https://unlocode.unece.org/publications/',
                 'edition': '2025-1',
                 'license': 'Official site states UN/CEFACT standards are CC BY 4.0; dataset-specific terms are not separately declared.',
                 'license_url': 'https://unlocode.unece.org/terms/'},
    'wpi': {'id': 'wpi', 'title': 'NGA World Port Index', 'url': 'https://msi.nga.mil/Publications/WPI',
            'edition': 'live FeatureServer snapshot', 'license': 'NGA publicly released reference data; attribution retained',
            'license_url': 'https://msi.nga.mil/Publications/WPI'},
}
LIMITATIONS = [
    '총계는 항구·항만 위치·시설 레코드 수입니다. 같은 UN/LOCODE의 개별 WPI 시설은 별도로 포함될 수 있습니다.',
    'UN/LOCODE의 항만 기능과 NGA WPI 수록 범위를 합쳤으며 모든 어항·마리나·신규 항만의 완전 수록을 보증하지 않습니다.',
    '좌표는 위치 참조점입니다. UN/LOCODE는 도시·지역 대표점(1분 단위)일 수 있고 선석·항구 입구·안전한 항로점이 아닙니다.',
    '항구 선택은 출발·도착 참조점만 입력합니다. 중간 항로·항만 접근·수심·통항 가능성은 별도로 검토해야 합니다.',
    '항만 기능1에는 과거 판본의 내륙 수운항이 남아 있을 수 있으므로 해항/내륙항을 추정해 분류하지 않았습니다.',
    '좌표 누락·불일치 자료는 검색에 남기며 지도나 항로 입력에 추정 좌표를 사용하지 않습니다.',
]
# NGA publishes GENC country names, not ISO alpha2 in this field. These explicit
# name mappings associate territories with the UN directory; never treat FIPS as ISO.
COUNTRY_ALIASES = {
    'United States': 'US', 'Russia': 'RU', 'North Korea': 'KP', 'South Korea': 'KR',
    'Philippines': 'PH', 'Brunei': 'BN', 'United Arab Emirates': 'AE', 'Tanzania': 'TZ',
    'Federated States of Micronesia': 'FM', 'Midway Islands': 'UM', 'Johnson Atoll': 'UM',
    'Congo (Kinshasa)': 'CD', 'The Gambia': 'GM', 'Syria': 'SY', 'Turkey': 'TR', 'Iran': 'IR',
    'Venezuela': 'VE', 'Saint Vincent and the Grenadines (Windward Islands)': 'VC',
    'U.S. Virgin Islands': 'VI', 'Turks and Caicos Islands': 'TC', 'The Bahamas': 'BS',
    'Sint Maarten': 'SX', 'Svalbard': 'SJ', 'Taiwan': 'TW', 'Congo (Brazzaville)': 'CG',
    'Burma': 'MM', 'Sudan': 'SD', 'South Georgia and South Sandwich Islands': 'GS',
    'Macau': 'MO', 'Marshall Islands': 'MH', 'Caribbean Netherlands': 'BQ', 'Cabo Verde': 'CV',
    'British Virgin Islands': 'VG', 'Falkland Islands (Islas Malvinas)': 'FK',
    'Northern Mariana Islands': 'MP', 'Wake Island': 'UM',
}
# Search conveniences only; not official UNECE/NGA names or translated records.
KOREAN_ALIASES = {
    'KRPUS': ['부산', '부산항', 'Busan'], 'KRINC': ['인천', '인천항'], 'KRUSN': ['울산', '울산항'],
    'KRKAN': ['광양', '광양항'], 'KRPTK': ['평택', '평택항'], 'KRKPO': ['포항', '포항항'],
    'KRMOK': ['목포', '목포항'], 'KRYOS': ['여수', '여수항'], 'KRGIN': ['경인항'],
    'SGSIN': ['싱가포르', '싱가포르항'], 'CNSGH': ['상하이', '상해'], 'CNSHG': ['상하이항'],
    'CNNBO': ['닝보'], 'CNNBG': ['닝보항'], 'CNSNZ': ['선전', '심천'], 'CNSZP': ['선전항'],
    'CNQIN': ['칭다오', '청도'], 'CNQDG': ['칭다오항'], 'CNDAL': ['다롄', '대련'], 'CNDAG': ['다롄항'],
    'CNTXG': ['톈진', '천진'], 'HKHKG': ['홍콩'], 'TWKHH': ['가오슝'],
    'JPTYO': ['도쿄', '동경'], 'JPYOK': ['요코하마'], 'JPUKB': ['고베'], 'JPOSA': ['오사카'],
    'USLAX': ['로스앤젤레스', '로스엔젤레스', 'LA항'], 'USLGB': ['롱비치'],
    'USNYC': ['뉴욕'], 'USHOU': ['휴스턴'], 'USSEA': ['시애틀'], 'CAVAN': ['밴쿠버'],
    'NLRTM': ['로테르담'], 'BEANR': ['앤트워프', '안트베르펜'], 'DEHAM': ['함부르크'],
    'GBFXT': ['펠릭스토'], 'GRPIR': ['피레우스'], 'EGPSD': ['포트사이드'],
    'AEJEA': ['제벨알리', '제벨 알리'], 'AE DXB'.replace(' ', ''): ['두바이'],
    'LKCMB': ['콜롬보'], 'INBOM': ['뭄바이'], 'AUSYD': ['시드니'], 'AUMEL': ['멜버른'],
    'ZADUR': ['더반'], 'ZACPT': ['케이프타운'], 'BRSSZ': ['산투스'], 'PABLB': ['발보아'],
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def atomic(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_manifest(folder: Path, meta: dict):
    """Retain the exact metadata version before advancing the current pointer."""
    data = encoded(meta)
    digest = hashlib.sha256(data).hexdigest()
    atomic(folder / 'manifests' / f'{digest}.json', data)
    atomic(folder / 'manifest.json', data)


def coordinate(lat, lon):
    try:
        if isinstance(lat, bool) or isinstance(lon, bool):
            return None
        lat, lon = float(lat), float(lon)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    if lat == 0 and lon == 0:
        return None
    return round(lat, 8), round(lon, 8)


def un_coordinate(text):
    match = re.fullmatch(r'(\d{2})(\d{2})([NS])\s+(\d{3})(\d{2})([EW])', text.strip())
    if not match:
        return None
    a, b, ns, c, d, ew = match.groups()
    if int(b) >= 60 or int(d) >= 60:
        return None
    return coordinate((int(a) + int(b) / 60) * (-1 if ns == 'S' else 1),
                      (int(c) + int(d) / 60) * (-1 if ew == 'W' else 1))


def un_code(value):
    value = re.sub(r'\s+', '', value or '').upper()
    return value if re.fullmatch(r'[A-Z]{2}[A-Z2-9]{3}', value) else None


def parse_unlocode(path: Path):
    groups, countries, counts = defaultdict(list), {}, Counter()
    with zipfile.ZipFile(path) as archive:
        names = sorted(n for n in archive.namelist() if re.search(r'CodeListPart[123]\.csv$', n))
        if len(names) != 3:
            raise ValueError('UN/LOCODE archive must contain all three CSV parts')
        for name in names:
            for row_number, row in enumerate(csv.reader(io.StringIO(archive.read(name).decode('utf-8-sig'))), 1):
                counts['raw_rows'] += 1
                if len(row) != 12:
                    raise ValueError(f'Unexpected UN/LOCODE schema: {name}:{row_number}')
                change, cc, loc, label, no_accent, subdiv, functions, status, date, iata, coord, remarks = row
                if not loc and label.startswith('.'):
                    countries[cc] = label[1:]
                    continue
                if not (functions.startswith('1') or (len(functions) == 8 and functions[7] == '8')):
                    continue
                counts['port_function_rows'] += 1
                if change.upper() == 'X' or status in ('XX', 'RQ'):
                    counts['excluded_deleted_or_pending'] += 1
                    continue
                code = un_code(cc + loc)
                if not code:
                    counts['excluded_invalid_code'] += 1
                    continue
                xy = un_coordinate(coord)
                counts['missing_or_invalid_coordinate_rows'] += xy is None
                groups[code].append({'name': label, 'alternate': no_accent, 'coordinate': xy,
                    'provenance': {'id': 'unlocode', 'edition': '2025-1', 'record_key': code,
                        'file': name, 'row': row_number, 'function': functions, 'status': status,
                        'change': change, 'date': date, 'coordinates_raw': coord,
                        'lat': xy[0] if xy else None, 'lon': xy[1] if xy else None,
                        'subdivision': subdiv, 'remarks': remarks}})
    ports = {}
    for code, entries in sorted(groups.items()):
        coordinates = {e['coordinate'] for e in entries if e['coordinate'] is not None}
        # Alias listings with conflicting coordinates cannot be silently chosen.
        xy = next(iter(coordinates)) if len(coordinates) == 1 else None
        counts['duplicate_listing_rows'] += len(entries) - 1
        counts['coordinate_conflict_codes'] += len(coordinates) > 1
        names = list(dict.fromkeys(n for e in entries for n in (e['name'], e['alternate']) if n))
        aliases = list(dict.fromkeys([*names[1:], *KOREAN_ALIASES.get(code, [])]))
        ports[code] = {'id': f'unlocode:{code}', 'name': names[0], 'aliases': aliases,
            'country_code': code[:2], 'country_name': countries.get(code[:2], code[:2]),
            'unlocode': code, 'wpi_id': None, 'lat': xy[0] if xy else None, 'lon': xy[1] if xy else None,
            'coordinate_source': 'unlocode' if xy else None, 'kind': 'inland' if all(e['provenance']['function'][7:8] == '8' and not e['provenance']['function'].startswith('1') for e in entries) else 'port',
            'coordinate_status': 'conflict' if len(coordinates) > 1 else 'reference' if xy else 'missing',
            'sources': [e['provenance'] for e in entries],
            'curated_aliases': KOREAN_ALIASES.get(code, [])}
    return ports, countries, dict(counts)


def country_key(value):
    return re.sub(r'[^a-z]', '', fold(value))


def unlocode_statuses(path):
    statuses = defaultdict(set)
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not re.search(r'CodeListPart[123]\.csv$', name):
                continue
            for row in csv.reader(io.StringIO(archive.read(name).decode('utf-8-sig'))):
                code = un_code(row[1] + row[2])
                if code:
                    state = 'deleted' if row[0].upper() == 'X' or row[7] == 'XX' else 'pending' if row[7] == 'RQ' else 'listed'
                    statuses[code].add(state)
    return {code: 'deleted' if values == {'deleted'} else 'pending' if values == {'pending'} else 'listed'
            for code, values in statuses.items()}


def parse_wpi(paths, countries, code_statuses=None):
    country_lookup = {country_key(name): cc for cc, name in countries.items()}
    country_lookup.update({country_key(name): cc for name, cc in COUNTRY_ALIASES.items()})
    features, object_ids = [], set()
    for path in paths:
        page = json.loads(Path(path).read_text())
        if 'error' in page or 'features' not in page:
            raise ValueError('NGA response is not a complete feature page')
        for feature in page['features']:
            object_id = feature['attributes']['objectid']
            if object_id in object_ids:
                raise ValueError('NGA pagination repeated an object ID')
            object_ids.add(object_id)
            features.append(feature)
    duplicated = Counter(f['attributes']['wpinumber'] for f in features)
    result, counts = [], Counter()
    for feature in features:
        a = feature['attributes']
        cc = country_lookup.get(country_key(a.get('wpi_cc') or ''))
        if not cc or cc not in countries:
            raise ValueError(f'Unmapped NGA GENC country name: {a.get("wpi_cc")}')
        index = float(a['wpinumber'])
        if not math.isfinite(index) or index <= 0 or not index.is_integer():
            raise ValueError('Invalid WPI record number')
        index = str(int(index))
        code = un_code(a.get('unlocode'))
        if code and code[:2] != cc:
            counts['unlocode_country_conflicts'] += 1
            code = None
        geometry = feature.get('geometry') or {}
        xy = coordinate(geometry.get('y'), geometry.get('x'))
        record_id = f'wpi:{index}'
        if duplicated[float(index)] > 1:
            # Retain distinct records where NGA itself reused a WPI number.
            global_id = re.sub(r'[^a-zA-Z0-9]', '', a.get('globalid') or '')
            if not global_id:
                raise ValueError('Duplicate WPI number lacks a stable GlobalID')
            record_id += '-' + global_id.lower()
        alt = a.get('alternate_name')
        result.append({'id': record_id, 'name': a['main_port_name'],
            'aliases': list(dict.fromkeys(([alt] if alt else []) + KOREAN_ALIASES.get(code, []))),
            'country_code': cc, 'country_name': countries[cc], 'unlocode': code, 'wpi_id': index,
            'unlocode_status': (code_statuses or {}).get(code, 'not_listed') if code else None,
            'lat': xy[0] if xy else None, 'lon': xy[1] if xy else None,
            'coordinate_source': 'wpi' if xy else None, 'coordinate_status': 'reference' if xy else 'missing',
            'kind': 'port', 'harbor_size': a.get('harbor_size_code'), 'harbor_type': a.get('harbor_type_code'),
            'sources': [{'id': 'wpi', 'edition': 'live FeatureServer snapshot', 'record_key': index,
                'global_id': a.get('globalid'), 'object_id': a['objectid'], 'country_raw': a['wpi_cc'],
                'unlocode_raw': a.get('unlocode'), 'lat': xy[0] if xy else None, 'lon': xy[1] if xy else None}],
            'curated_aliases': KOREAN_ALIASES.get(code, [])})
    counts['raw_features'] = len(features)
    counts['reused_wpi_numbers'] = sum(n > 1 for n in duplicated.values())
    return result, dict(counts)


def consolidate(un_ports, wpi_ports):
    counts = Counter()
    candidates = Counter(p['unlocode'] for p in wpi_ports if p['unlocode'])
    ports = dict(un_ports)
    for wpi in wpi_ports:
        code = wpi['unlocode']
        target = ports.get(code)
        distance = None
        if target and target['lat'] is not None and wpi['lat'] is not None:
            distance = Geodesic.WGS84.Inverse(target['lat'], target['lon'], wpi['lat'], wpi['lon'])['s12'] / 1000
        if (target and candidates[code] == 1 and target['coordinate_status'] != 'conflict'
                and (distance is None or distance <= 25)):
            target['aliases'] = list(dict.fromkeys([*target['aliases'], wpi['name'], *wpi['aliases']]))
            target['sources'].extend(wpi['sources'])
            target['wpi_id'] = wpi['wpi_id']
            target['harbor_size'], target['harbor_type'] = wpi['harbor_size'], wpi['harbor_type']
            if wpi['lat'] is not None:
                for key in ('lat', 'lon', 'coordinate_source', 'coordinate_status'):
                    target[key] = wpi[key]
            target['merge_rule'] = 'exact_unlocode_unique_wpi_within_25km_or_missing_un_coordinate'
            target['source_coordinate_distance_km'] = round(distance, 3) if distance is not None else None
            counts['consolidated_unique_code'] += 1
        else:
            wpi['related_unlocode'] = code if code in un_ports else None
            if target and distance is not None and distance > 25:
                wpi['source_coordinate_distance_km'] = round(distance, 3)
                wpi['merge_status'] = 'coordinate_disagreement'
                wpi['coordinate_conflict'] = {'distance_km': round(distance, 3), 'other_port_ids': [target['id']]}
                previous = target.get('coordinate_conflict', {'distance_km': 0, 'other_port_ids': []})
                target['coordinate_conflict'] = {
                    'distance_km': round(max(previous['distance_km'], distance), 3),
                    'other_port_ids': [*previous['other_port_ids'], wpi['id']]}
                target['merge_status'] = 'coordinate_disagreement'
                counts['distant_same_code_preserved'] += 1
            elif target:
                wpi['merge_status'] = 'multiple_facilities_or_conflicting_reference'
            ports[wpi['id']] = wpi
    result = sorted(ports.values(), key=lambda p: p['id'])
    if len({p['id'] for p in result}) != len(result):
        raise ValueError('Duplicate final catalog identities')
    return result, dict(counts)


def acquire(folder: Path):
    snapshot = folder / 'raw' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    snapshot.mkdir(parents=True, exist_ok=False)
    files = []

    def download(name, url, source):
        destination = snapshot / name
        subprocess.run(['curl', '-fLsS', '--retry', '2', '--connect-timeout', '20', '--max-time', '120', url, '-o', str(destination)], check=True)
        data = destination.read_bytes()
        files.append({'source': source, 'path': str(destination.relative_to(folder)), 'url': url,
                      'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'retrieved_at': utc_now()})
        return destination

    download('unlocode-2025-1.zip', UN_URL, 'unlocode')
    download('wpi-layer.json', WPI_LAYER + '?f=pjson', 'wpi')
    count_path = download('wpi-count.json', WPI_LAYER + '/query?' + urlencode({'where': '1=1', 'returnCountOnly': 'true', 'f': 'json'}), 'wpi')
    count = json.loads(count_path.read_text())['count']
    download('wpi-object-ids.json', WPI_LAYER + '/query?' + urlencode({'where': '1=1', 'returnIdsOnly': 'true', 'f': 'json'}), 'wpi')
    for offset in range(0, count, 1000):
        download(f'wpi-features-{offset}.json', WPI_LAYER + '/query?' + urlencode({
            'where': '1=1', 'outFields': '*', 'outSR': '4326', 'returnGeometry': 'true',
            'orderByFields': 'objectid ASC', 'resultOffset': offset, 'resultRecordCount': 1000, 'f': 'json'}), 'wpi')
    inventory = {'schema_version': 'ports-acquisition-1.0', 'acquired_at': utc_now(), 'wpi_expected_count': count, 'files': files}
    # Verify/parsing is performed before switching the authoritative raw pointer.
    build_catalog(folder, inventory)
    atomic(folder / 'raw' / 'acquisition.json', encoded(inventory))
    return inventory


def build_catalog(folder: Path, inventory=None):
    inventory = inventory or json.loads((folder / 'raw' / 'acquisition.json').read_text())
    files = inventory['files']
    for item in files:
        path = folder / item['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Raw source checksum mismatch: ' + item['path'])
    un_file = next(folder / f['path'] for f in files if f['source'] == 'unlocode' and f['path'].endswith('.zip'))
    un_ports, countries, un_counts = parse_unlocode(un_file)
    wpi_ports, wpi_counts = parse_wpi([folder / f['path'] for f in files if '/wpi-features-' in f['path']], countries, unlocode_statuses(un_file))
    wpi_counts['references_to_deleted_unlocodes'] = sum(p['unlocode_status'] == 'deleted' for p in wpi_ports)
    ids_path = next(folder / f['path'] for f in files if f['path'].endswith('/wpi-object-ids.json'))
    expected_ids = json.loads(ids_path.read_text())['objectIds']
    actual_ids = [p['sources'][0]['object_id'] for p in wpi_ports]
    if len(expected_ids) != len(set(expected_ids)) or set(actual_ids) != set(expected_ids):
        raise ValueError('NGA feature pages do not match the independent full object-ID inventory')
    if len(wpi_ports) != inventory['wpi_expected_count']:
        raise ValueError('NGA page coverage disagrees with the independently queried total')
    ports, merge_counts = consolidate(un_ports, wpi_ports)
    if len(ports) < 10000 or len(wpi_ports) < 3000:
        raise ValueError('Source unexpectedly incomplete; prior catalog retained')
    data = encoded({'schema_version': 'ports-1.0', 'ports': ports})
    digest = hashlib.sha256(data).hexdigest()
    source_meta = []
    for source_id, info in SOURCE_INFO.items():
        source_files = [f for f in files if f['source'] == source_id]
        source_meta.append({**info, 'retrieved_at': max(f['retrieved_at'] for f in source_files),
            'sha256': hashlib.sha256(encoded([{'path': f['path'], 'sha256': f['sha256']} for f in source_files])).hexdigest(),
            'files': source_files, 'records': len(un_ports) if source_id == 'unlocode' else len(wpi_ports)})
    by_country = Counter(p['country_code'] for p in ports)
    meta = {'schema_version': 'ports-1.0', 'status': 'ready', 'catalog_id': 'ports-' + digest[:16],
        'catalog_sha256': digest, 'built_at': utc_now(), 'total': len(ports),
        'located': sum(p['lat'] is not None for p in ports),
        'missing_coordinates': sum(p['lat'] is None for p in ports),
        'count_unit': 'port_location_and_facility_records',
        'countries': [{'code': cc, 'name': countries[cc], 'count': count} for cc, count in sorted(by_country.items())],
        'sources': source_meta, 'quality': {'unlocode': un_counts, 'wpi': wpi_counts, 'consolidation': merge_counts},
        'limitations': LIMITATIONS,
        'merge_policy': 'Exact UN/LOCODE only, one WPI candidate, coordinates within 25km or missing UN reference. Multiple facilities and conflicts remain separate; no fuzzy name merging.',
        'coordinate_policy': 'NGA reference preferred for eligible consolidation. Authoritative UN DDMMN DDDMME otherwise. Never infer, geocode, or snap coordinates.'}
    atomic(folder / 'catalogs' / f'{digest}.json', data)
    publish_manifest(folder, meta)
    return meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--download', action='store_true')
    mode.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    if args.download:
        acquire(root())
        meta = json.loads((root() / 'manifest.json').read_text())
    else:
        meta = build_catalog(root())
    print(json.dumps({k: meta[k] for k in ('catalog_id', 'total', 'located', 'missing_coordinates', 'quality')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
