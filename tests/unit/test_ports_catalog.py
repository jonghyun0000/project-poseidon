"""Port reference identities, source conflicts, offline integrity, and API contracts."""
import csv
import hashlib
import io
import json
import unicodedata
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from poseidon.ports import catalog
from poseidon.ports.build import (coordinate, un_coordinate, parse_unlocode, parse_wpi,
                                 consolidate, unlocode_statuses, encoded, atomic, build_catalog,
                                 publish_manifest)
from poseidon.api.ports import router


def un_zip(tmp_path, rows):
    path = tmp_path / 'un.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        for part in range(1, 4):
            output = io.StringIO()
            writer = csv.writer(output)
            if part == 1:
                writer.writerow(['', 'KR', '', '.KOREA, REPUBLIC OF', '', '', '', '', '', '', '', ''])
                writer.writerows(rows)
            archive.writestr(f'csv/UNLOCODE CodeListPart{part}.csv', output.getvalue())
    return path


def row(code='PUS', name='Busan', functions='1-------', coord='3506N 12903E', status='AA', change=''):
    return [change, 'KR', code, name, name, '', functions, status, '2501', '', coord, '']


@pytest.mark.parametrize('text', ['9060N 18000E', '9001N 18000E', '8960N 18000E',
                                  '0000N 18001W', '3500X 12900E', '0000N 00000E', '', '35.0,129.0'])
def test_invalid_compact_coordinates_are_missing(text):
    assert un_coordinate(text) is None


def test_coordinate_sign_minutes_and_dateline():
    assert un_coordinate('2530S 17930W') == (-25.5, -179.5)
    assert un_coordinate('9000N 18000E') == (90, 180)
    assert coordinate(float('nan'), 1) is None
    assert coordinate(1, float('inf')) is None
    assert coordinate(True, 1) is None


def test_alias_listing_uses_only_valid_coordinate_and_keeps_all_source_rows(tmp_path):
    path = un_zip(tmp_path, [row(name='Pusan', coord=''), row(name='Busan')])
    ports, countries, counts = parse_unlocode(path)
    p = ports['KRPUS']
    assert p['lat'] == 35.1 and 'Busan' in p['aliases'] and '부산' in p['aliases']
    assert len(p['sources']) == 2 and p['sources'][0]['coordinates_raw'] == ''
    assert counts['duplicate_listing_rows'] == 1
    assert p['kind'] == 'port'  # Historical function1 does not prove maritime-only.


def test_deleted_pending_airports_excluded_but_missing_legacy_status_preserved(tmp_path):
    path = un_zip(tmp_path, [row(code='DEL', change='X'), row(code='REQ', status='RQ'),
                            row(code='AIR', functions='---4----'), row(status='QQ', coord='')])
    ports, _, counts = parse_unlocode(path)
    assert set(ports) == {'KRPUS'}
    assert ports['KRPUS']['sources'][0]['status'] == 'QQ'
    assert ports['KRPUS']['lat'] is None
    assert counts['excluded_deleted_or_pending'] == 2
    assert unlocode_statuses(path)['KRDEL'] == 'deleted'


def test_conflicting_un_alias_coordinates_are_not_arbitrarily_selected(tmp_path):
    ports, _, _ = parse_unlocode(un_zip(tmp_path, [row(), row(coord='3606N 12903E')]))
    assert ports['KRPUS']['coordinate_status'] == 'conflict'
    assert ports['KRPUS']['lat'] is None
    assert len(ports['KRPUS']['sources']) == 2


def wpi_feature(object_id=1, index=100, code='KR PUS', lat=35.1, lon=129.05, country='South Korea'):
    return {'attributes': {'objectid': object_id, 'wpinumber': index, 'globalid': f'abc-{object_id}',
        'unlocode': code, 'wpi_cc': country, 'main_port_name': f'Terminal {object_id}',
        'harbor_size_code': 'Large', 'harbor_type_code': 'Coastal (Natural)'},
        'geometry': {'x': lon, 'y': lat}}


def wpi_parse(tmp_path, features, statuses=None):
    path = tmp_path / 'wpi.json'
    path.write_text(json.dumps({'features': features}))
    return parse_wpi([path], {'KR': 'KOREA, REPUBLIC OF'}, statuses)


def test_wpi_number_reuse_preserves_global_identity(tmp_path):
    ports, quality = wpi_parse(tmp_path, [wpi_feature(), wpi_feature(object_id=2)])
    assert len({p['id'] for p in ports}) == 2
    assert all(p['wpi_id'] == '100' for p in ports)
    assert quality['reused_wpi_numbers'] == 1


def test_wpi_pagination_duplicates_fail_closed(tmp_path):
    with pytest.raises(ValueError, match='object ID'):
        wpi_parse(tmp_path, [wpi_feature(), wpi_feature()])


def test_wpi_country_conflict_and_retired_code_are_traceable(tmp_path):
    ports, quality = wpi_parse(tmp_path, [wpi_feature(code='US PUS'), wpi_feature(object_id=2, index=101)], {'KRPUS': 'deleted'})
    assert ports[0]['unlocode'] is None
    assert ports[0]['sources'][0]['unlocode_raw'] == 'US PUS'
    assert quality['unlocode_country_conflicts'] == 1
    assert ports[1]['unlocode_status'] == 'deleted'


def test_merge_exact_unique_reference_preserves_both_raw_coordinates(tmp_path):
    un, _, _ = parse_unlocode(un_zip(tmp_path, [row()]))
    wpi, _ = wpi_parse(tmp_path, [wpi_feature(lat=35.11)])
    ports, counts = consolidate(un, wpi)
    assert len(ports) == 1 and ports[0]['id'] == 'unlocode:KRPUS'
    assert ports[0]['lat'] == 35.11 and ports[0]['coordinate_source'] == 'wpi'
    assert [s['lat'] for s in ports[0]['sources']] == [35.1, 35.11]
    assert counts['consolidated_unique_code'] == 1


def test_multiple_facilities_and_far_conflicts_are_not_merged(tmp_path):
    un, _, _ = parse_unlocode(un_zip(tmp_path, [row()]))
    wpi, _ = wpi_parse(tmp_path, [wpi_feature(lat=40), wpi_feature(object_id=2, index=101)])
    ports, counts = consolidate(un, wpi)
    assert len(ports) == 3
    by_id = {p['id']: p for p in ports}
    for port_id in ('unlocode:KRPUS', 'wpi:100'):
        assert by_id[port_id]['coordinate_conflict']['distance_km'] > 500
        assert by_id[port_id]['merge_status'] == 'coordinate_disagreement'
    assert by_id['unlocode:KRPUS']['lat'] == 35.1


@pytest.fixture
def installed(tmp_path, monkeypatch):
    ports, _, _ = parse_unlocode(un_zip(tmp_path, [row(), row(code='MIS', name='São Sebastião', coord='')]))
    payload = encoded({'ports': list(ports.values())})
    digest = hashlib.sha256(payload).hexdigest()
    atomic(tmp_path / 'catalogs' / f'{digest}.json', payload)
    atomic(tmp_path / 'manifest.json', encoded({'catalog_id': digest[:16], 'catalog_sha256': digest,
        'status': 'ready', 'total': 2, 'located': 1}))
    monkeypatch.setattr(catalog, 'root', lambda: tmp_path)
    catalog._load.cache_clear()
    yield tmp_path, digest
    catalog._load.cache_clear()


def test_offline_search_normalizes_unicode_and_spaced_codes(installed):
    for query in ('KR PUS', 'krpus', '부산', unicodedata.normalize('NFD', '부산')):
        assert catalog.search_ports(query)['items'][0]['id'] == 'unlocode:KRPUS'
    assert catalog.search_ports('Sao Sebastiao')['total'] == 1
    assert catalog.search_ports(country='US')['total'] == 0
    assert len(catalog.ports_geojson()['features']) == 1


def test_canonical_snapshot_is_single_version_and_defensive_copy(installed, monkeypatch):
    original = catalog._catalog
    calls = []
    monkeypatch.setattr(catalog, '_catalog', lambda: (calls.append(True), original())[1])
    meta, ports = catalog.catalog_snapshot(['unlocode:KRPUS', 'absent'])
    assert len(calls) == 1 and ports['absent'] is None
    ports['unlocode:KRPUS']['lat'] = 0
    assert catalog.get_port('unlocode:KRPUS')['lat'] == 35.1


def test_corruption_and_unavailable_do_not_serve_partial_data(installed):
    folder, digest = installed
    (folder / 'catalogs' / f'{digest}.json').write_text('{}')
    catalog._load.cache_clear()
    assert catalog.catalog_meta()['status'] == 'unavailable'
    with pytest.raises(catalog.CatalogUnavailable):
        catalog.search_ports('Busan')


def test_raw_checksum_failure_preserves_prior_manifest(tmp_path):
    atomic(tmp_path / 'manifest.json', b'{"previous":true}')
    (tmp_path / 'raw.json').write_text('{}')
    inventory = {'files': [{'path': 'raw.json', 'sha256': '0' * 64}]}
    with pytest.raises(ValueError, match='checksum'):
        build_catalog(tmp_path, inventory)
    assert json.loads((tmp_path / 'manifest.json').read_text()) == {'previous': True}


def test_api_search_geojson_detail_limits_and_unavailable(installed):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get('/v1/ports', params={'q': '부산'}).json()['items'][0]['id'] == 'unlocode:KRPUS'
    assert client.get('/v1/ports/geojson').json()['features'][0]['geometry']['coordinates'] == [129.05, 35.1]
    assert client.get('/v1/ports/unlocode:KRMIS').json()['lat'] is None
    assert client.get('/v1/ports/unknown').status_code == 404
    for params in ({'limit': 101}, {'offset': -1}, {'country': '../../'}, {'q': 'x' * 161}):
        assert client.get('/v1/ports', params=params).status_code == 422
    (installed[0] / 'manifest.json').unlink()
    assert client.get('/v1/ports/meta').json()['status'] == 'unavailable'
    assert client.get('/v1/ports').status_code == 503


def test_chinese_search_aliases_target_port_codes_not_iata_airports():
    from poseidon.ports.build import KOREAN_ALIASES
    assert '상하이' in KOREAN_ALIASES['CNSGH']
    assert not {'CNSHA', 'CNNGB', 'CNSZX', 'CNTAO', 'CNDLC'} & set(KOREAN_ALIASES)


def test_rebuild_metadata_versions_are_content_addressed_and_preserved(tmp_path):
    first = {'catalog_sha256': '1' * 64, 'built_at': '2026-09-12T08:00:00Z',
             'sources': [{'id': 'wpi', 'sha256': '2' * 64}]}
    second = {**first, 'built_at': '2026-09-12T09:00:00Z'}
    publish_manifest(tmp_path, first)
    publish_manifest(tmp_path, second)
    for expected in (first, second):
        data = encoded(expected)
        path = tmp_path / 'manifests' / f'{hashlib.sha256(data).hexdigest()}.json'
        assert path.read_bytes() == data
    assert (tmp_path / 'manifest.json').read_bytes() == encoded(second)


def test_failed_metadata_archive_does_not_advance_current_manifest(tmp_path, monkeypatch):
    from poseidon.ports import build
    previous = {'catalog_sha256': '1' * 64, 'built_at': 'old'}
    publish_manifest(tmp_path, previous)
    original = build.atomic

    def failing_archive(path, data):
        if path.parent.name == 'manifests':
            raise OSError('archive storage unavailable')
        original(path, data)

    monkeypatch.setattr(build, 'atomic', failing_archive)
    with pytest.raises(OSError, match='archive storage'):
        publish_manifest(tmp_path, {**previous, 'built_at': 'new'})
    assert json.loads((tmp_path / 'manifest.json').read_text()) == previous
