"""Observation matching and descriptive accuracy must not manufacture coverage."""

import gzip
import hashlib
import math

import httpx
import numpy as np
import pandas as pd
import pytest

from poseidon.validation import global_audit as ga
from poseidon.validation import global_metrics as gm
from poseidon.validation import global_observations as go

pytestmark = pytest.mark.unit


def ndbc_text(rows):
    return '#YY MM DD hh mm WVHT\n#yr mo dy hr mn m\n' + '\n'.join(rows) + '\n'


def paired_row(station='A', cycle='20260901T00', observed=2., forecast=3., **kwargs):
    return {
        'station_id': station, 'cycle': cycle,
        'observation_time': '2026-09-02T00:00:00+00:00',
        'valid_time': '2026-09-02T00:00:00+00:00',
        'lead_h': 24, 'region': 'north_pacific', 'lat': 45., 'lon': -150.,
        'observed_hs_m': observed, 'forecast_hs_m': forecast, **kwargs,
    }


def test_released_hs_retains_calm_repeats_and_counts_exclusions():
    data, counts = go.parse_hs(ndbc_text([
        '2026 09 01 00 00 MM',
        '2026 09 01 01 00 -0.1',
        '2026 09 01 02 00 99.0',
        '2026 09 01 03 00 inf',
        '2026 09 32 04 00 1.0',
        '2026 09 01 05 00 0.0',
        '2026 09 01 06 00 0.0',
        '2026 09 01 07 00 40.0',
    ]))
    assert data.hs.tolist() == [0., 0., 40.]
    assert str(data.ts.dt.tz) == 'UTC'
    assert counts['rows'] == 8
    assert counts['missing_hs'] == 2
    assert counts['outside_gross_range'] == 2
    assert counts['invalid_time'] == 1
    assert counts['usable_rows'] == 3


def test_conflicting_duplicate_is_fully_excluded_without_arbitrary_winner():
    data, counts = go.parse_hs(ndbc_text([
        '2026 09 01 02 00 2.0',
        '2026 09 01 00 00 1.0',
        '2026 09 01 01 00 3.0',
        '2026 09 01 00 00 1.0',
        '2026 09 01 01 00 4.0',
        '2026 09 01 01 00 3.0',
    ]))
    assert data.hs.tolist() == [1., 2.]
    assert counts['conflicting_timestamp_rows'] == 3
    assert counts['identical_duplicate_rows'] == 1
    assert counts['usable_rows'] == 2


@pytest.mark.parametrize('text', ['not a data product', '#YY MM DD hh mm WSPD\n'])
def test_missing_observation_schema_is_an_error(text):
    with pytest.raises(ValueError):
        go.parse_hs(text)


def test_nearest_observation_tie_uses_earlier_nominal_time_and_retains_offset():
    data, _ = go.parse_hs(ndbc_text([
        '2026 09 01 06 30 9.0', '2026 09 01 05 30 2.0',
    ]))
    result = go.nearest_observation(data, '2026-09-01T15:00+09:00', '2026-09-01T07:00Z')
    assert result == {
        'observation_time': '2026-09-01T05:30:00+00:00',
        'observed_hs_m': 2., 'time_offset_minutes': -30.,
    }
    assert go.nearest_observation(data, '2026-09-01T04:59Z', '2026-09-01T07:00Z') is None


def test_future_observation_cannot_win_nearest_match():
    data, _ = go.parse_hs(ndbc_text([
        '2026 09 01 05 40 2.0', '2026 09 01 06 10 9.0',
    ]))
    result = go.nearest_observation(data, '2026-09-01T06:00Z', '2026-09-01T06:00Z')
    assert result['observed_hs_m'] == 2.
    assert result['time_offset_minutes'] == -20.
    assert go.nearest_observation(data, '2026-09-01T06:00Z', '2026-09-01T05:00Z') is None
    assert go.nearest_observation(data.iloc[:0], '2026-09-01T06:00Z', '2026-09-01T07:00Z') is None


def test_deployment_uses_history_and_excludes_entire_transition_date():
    station = {'lat': 10., 'lon': 20., 'history': [
        {'start': '2026-01-01', 'stop': '2026-09-03', 'lat': '30', 'lng': '-150'},
        {'start': '2026-09-03', 'stop': '', 'lat': '35', 'lng': '-145'},
    ]}
    assert go.deployment(station, '2026-09-02T23:59:00Z')['lat'] == 30.
    for hour in ('00:00:00', '23:59:59'):
        assert go.deployment(station, f'2026-09-03T{hour}Z') is None
    assert go.deployment(station, '2026-09-04T00:00:00Z')['lon'] == -145.
    assert go.deployment(station, '2026-09-04T02:00:00+09:00') is None
    assert go.deployment(station, '2025-12-31T00:00:00Z') is None
    with pytest.raises(ValueError, match='timezone-aware'):
        go.deployment(station, '2026-09-04T00:00:00')


def test_unknown_invalid_or_overlapping_deployments_never_fall_back_to_current_coords():
    assert go.deployment({'lat': 30., 'lon': -150.}, '2026-09-01T00:00Z') is None
    invalid = {'start': '2026-01-01', 'stop': '', 'lat': '91', 'lng': '0'}
    assert go.deployment({'history': [invalid]}, '2026-09-01T00:00Z') is None
    first = {'start': '2026-01-01', 'stop': '', 'lat': '30', 'lng': '-150'}
    overlap = {'start': '2026-08-01', 'stop': '', 'lat': '31', 'lng': '-151'}
    assert go.deployment({'history': [first, overlap]}, '2026-09-01T00:00Z') is None


def test_observation_snapshot_joins_deployment_case_insensitively_and_archives_bytes(tmp_path, monkeypatch):
    active = b'''<stations created="2026-09-01T12:00:00UTC">
      <station id="alp01" lat="20" lon="30" type="buoy" met="y" owner="Partner"/>
      <station id="boat1" lat="20" lon="30" type="usv" met="y"/>
      <station id="off01" lat="20" lon="30" type="buoy" met="n"/>
    </stations>'''
    history = b'''<stations><station id="ALP01" type="other">
      <history start="2026-01-01" stop="" lat="21" lng="31"/>
    </station></stations>'''
    raw = ndbc_text(['2026 09 01 06 00 2.5']).encode()
    requests = []

    def handle(request):
        url = str(request.url)
        requests.append(url)
        payload = {go.STATIONS_URL: active, go.HISTORY_URL: history,
                   go.OBS_URL.format(station_id='alp01'): raw}
        assert url in payload, 'Tests must not access unlisted remote data'
        return httpx.Response(200, content=payload[url])

    original_client = httpx.Client
    monkeypatch.setattr(go.httpx, 'Client', lambda **kw: original_client(
        transport=httpx.MockTransport(handle), **kw))
    manifest, observations = go.acquire_observations(tmp_path)
    assert len(manifest['stations']) == 1
    record = manifest['stations'][0]
    assert go.deployment(record, '2026-09-01T06:00Z')['lat'] == 21.
    assert record['sha256'] == hashlib.sha256(raw).hexdigest()
    assert gzip.decompress((tmp_path/'alp01.txt.gz').read_bytes()) == raw
    assert observations['alp01'].hs.tolist() == [2.5]
    assert len(requests) == 3
    recovered_manifest, recovered_data = ga.read_observations(tmp_path)
    assert recovered_manifest['sha256'] == hashlib.sha256(active).hexdigest()
    assert recovered_data['alp01'].equals(observations['alp01'])
    (tmp_path/'alp01.txt.gz').write_bytes(gzip.compress(raw.replace(b'2.5', b'9.5')))
    with pytest.raises(ValueError, match='Observation checksum mismatch'):
        ga.read_observations(tmp_path)


@pytest.mark.parametrize('lat,lon,expected', [
    (45., -150., 'north_pacific'), (35., 179., 'north_pacific'),
    (35., -181., 'north_pacific'), (30., -75., 'north_atlantic'),
    (25., -90., 'north_atlantic'), (-25., 70., 'indian'),
    (15., 60., 'indian'), (38., 18., 'mediterranean'),
    (-25., -130., 'south_pacific'), (-25., -20., 'south_atlantic'),
    (-60., 70., 'southern'), (66.5, -150., 'arctic'),
])
def test_declared_geographic_regions(lat, lon, expected):
    assert gm.region(lat, lon) == expected


def test_metrics_match_hand_calculated_sample_weighted_errors():
    rows = [
        paired_row('A', observed=2., forecast=1.),
        paired_row('A', observed=2., forecast=4., observation_time='2026-09-02T01:00Z'),
        paired_row('B', observed=4., forecast=5.),
        paired_row('B', observed=8., forecast=8., observation_time='2026-09-02T01:00Z'),
    ]
    result = gm.metrics(rows)
    assert result['n'] == 4
    assert result['bias_m'] == .5
    assert result['mae_m'] == 1.
    assert result['rmse_m'] == pytest.approx(math.sqrt(1.5))
    assert result['centered_rmse_m'] == pytest.approx(math.sqrt(1.25))
    assert result['scatter_index'] == pytest.approx(math.sqrt(1.25)/4.)
    assert result['station_balanced_mean_rmse_m'] == pytest.approx(
        (math.sqrt(2.5)+math.sqrt(.5))/2.)
    assert result['observed_min_m'] == 2.
    assert result['observed_max_m'] == 8.


def test_overlapping_forecasts_do_not_inflate_unique_observation_count():
    rows = [paired_row(cycle=f'2026090{day}T00') for day in (1, 2, 3)]
    result = gm.metrics(rows)
    assert result['n'] == 3
    assert result['n_cycles'] == 3
    assert result['n_unique_observations'] == 1
    assert result['n_valid_times'] == 1
    assert result['n_stations'] == 1


def test_cluster_interval_does_not_shrink_from_repeating_identical_samples():
    rows = [paired_row(cycle=f'2026090{i+1}T00', observed=20., forecast=20.+err)
            for i, err in enumerate((-10., -10., 0., 10., 10.))]
    small = gm.metrics(rows, bootstrap=True)
    repeated = gm.metrics(rows*100, bootstrap=True)
    assert repeated['cycle_bootstrap_95'] == small['cycle_bootstrap_95']
    assert small['cycle_bootstrap_95']['bias_m'][0] < -4.
    assert small['cycle_bootstrap_95']['bias_m'][1] > 4.
    assert small['leave_one_cycle_out_rmse_m'][2] == 10.
    assert gm.metrics(rows[:4], bootstrap=True)['cycle_bootstrap_95'] is None


def test_empty_and_calm_samples_do_not_publish_zero_error_or_infinite_skill():
    empty = gm.metrics([])
    assert empty['n'] == 0
    assert empty['rmse_m'] is empty['bias_m'] is empty['mae_m'] is None
    assert empty['cycle_bootstrap_95'] is None
    calm = gm.metrics([paired_row(observed=0., forecast=1.)])
    assert calm['rmse_m'] == 1.
    assert calm['scatter_index'] is None


def test_summary_keeps_unobserved_regions_and_leads_explicit():
    report = gm.summarize([paired_row()], [
        {'station_id': 'A', 'lat': 45., 'lon': -150., 'name': 'Observed'},
        {'station_id': 'B', 'lat': -65., 'lon': 70., 'name': 'No data'},
    ], [6, 24, 120])
    south = next(r for r in report['by_region'] if r['region'] == 'southern')
    assert south['n'] == 0
    assert south['rmse_m'] is None
    assert {r['lead_h']: r['n'] for r in report['by_lead']} == {6: 0, 24: 1, 120: 0}
    absent = next(s for s in report['stations'] if s['station_id'] == 'B')
    assert absent['n'] == 0
    assert absent['rmse_m'] is None
    assert sum(b['n'] for b in report['by_hs']) == report['overall']['n']


def test_protocol_is_positive_lead_only_and_targets_stop_at_cutoff():
    p = ga.protocol('20260901T00', '20260902T00', '2026-09-02T15:00:00+09:00')
    assert p['cutoff_utc'] == '2026-09-02T06:00:00+00:00'
    assert p['leads_h'] == [6, 24, 48, 72, 120]
    assert ga.targets(p) == [('20260901T00', 6), ('20260901T00', 24), ('20260902T00', 6)]
    assert ga.targets({**p, 'cutoff_utc': '2026-09-01T05:59:59Z'}) == []


@pytest.mark.parametrize('start,end,cutoff', [
    ('20260902T00', '20260901T00', '2026-09-02T06:00Z'),
    ('20260901T06', '20260902T00', '2026-09-02T06:00Z'),
    ('20260701T00', '20260901T00', '2026-09-02T06:00Z'),
    ('20260901T00', '20260902T00', '2026-09-02T06:00'),
])
def test_protocol_rejects_ambiguous_or_out_of_scope_windows(start, end, cutoff):
    with pytest.raises(ValueError):
        ga.protocol(start, end, cutoff)


def test_protocol_rejects_future_cutoff():
    with pytest.raises(ValueError):
        ga.protocol('20260901T00', '20260902T00',
                    (pd.Timestamp.now(tz='UTC')+pd.Timedelta(days=1)).isoformat())


@pytest.mark.parametrize('mode', ['partial', 'all_frames_failed', 'all_obs_failed', 'code_changed'])
def test_audit_accounts_for_gaps_and_preserves_previous_report_on_failure(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(ga, 'root', lambda: tmp_path)
    p = ga.protocol('20260901T00', '20260902T00', '2026-09-02T06:00Z')
    stations = []
    for sid, lon in [('A', 0.), ('B', 90.), ('C', 180.), ('D', 180.), ('E', -90.)]:
        stations.append({
            'station_id': sid, 'name': sid, 'lat': 0., 'lon': lon, 'state': 'downloaded',
            'sha256': 'observation-source-hash', 'qc_counts': {'usable_rows': 1},
            'history': [{'start': '2026-09-01' if sid == 'C' else '2026-01-01',
                         'stop': '', 'lat': '0', 'lng': str(lon)}],
        })
    stations[3]['state'] = 'failed'
    if mode == 'all_obs_failed':
        for station in stations:
            station['state'] = 'failed'
    observations = {s['station_id']: go.parse_hs(ndbc_text(['2026 09 01 06 00 2.0']))[0]
                    for s in stations}
    manifest = {'stations': stations, 'sha256': 'catalog-hash', 'history_sha256': 'history-hash'}

    def acquire(folder):
        # Protocol must already be frozen before any observation is inspected.
        assert ga.read_json(folder.parent/'protocol.json')['id'] == p['id']
        folder.mkdir(parents=True, exist_ok=True)
        ga.atomic_json(folder/'manifest.json', manifest)
        return manifest, observations

    monkeypatch.setattr(ga, 'acquire_observations', acquire)
    monkeypatch.setattr(ga, 'land_points', lambda lats, lons: np.array([lons[0] == 90.]))
    data = {key: np.ones((3, 4)) for key in ga.FIELDS}
    data['hs'] = np.tile([3., 3., 3., np.nan], (3, 1))
    calls = []

    def frame(client, cycle, lead, offline=False):
        calls.append((cycle, lead))
        if mode == 'all_frames_failed' or (cycle, lead) == ('20260901T00', 24):
            raise FileNotFoundError('Archived frame unavailable')
        return data, {'cycle': cycle, 'lead_h': lead, 'sha256_grib': 'forecast-source-hash'}

    class NoNetworkClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args, **kwargs):
            pytest.fail('Audit unit test attempted a network request')

    monkeypatch.setattr(ga, 'forecast_frame', frame)
    monkeypatch.setattr(ga.httpx, 'Client', NoNetworkClient)
    previous = {'run_id': '20260831T000000Z', 'generated_at': '2026-08-31T00:00:00Z'}
    ga.atomic_json(tmp_path/'latest.json', previous)
    if mode == 'code_changed':
        fingerprint_calls = []

        def changing_fingerprint():
            fingerprint_calls.append(1)
            return {'source.py': 'old' if len(fingerprint_calls) == 1 else 'changed'}

        monkeypatch.setattr(ga, 'code_fingerprints', changing_fingerprint)
    if mode != 'partial':
        with pytest.raises(RuntimeError):
            ga.run(p)
        assert ga.read_json(tmp_path/'latest.json') == previous
        assert ga.read_json(tmp_path/'status.json')['state'] == 'failed'
        return
    report = ga.run(p)
    assert calls == [('20260901T00', 6), ('20260901T00', 24), ('20260902T00', 6)]
    assert report['overall']['n'] == 1
    assert report['overall']['rmse_m'] == 1.
    assert report['state'] == 'partial'
    assert report['coverage']['global_ready'] is False
    assert report['coverage']['valid_start'] == '2026-09-01T06:00:00+00:00'
    assert 24 in report['coverage']['unscored_leads_h']
    assert 384 in report['coverage']['unscored_leads_h']
    acquisition = report['acquisition']
    assert acquisition['forecast_frames_requested'] == 3
    assert acquisition['forecast_frames_available'] == 2
    assert acquisition['candidate_pairs'] == 15
    excluded = acquisition['exclusions']
    assert excluded['forecast_targets_not_yet_valid'] == 7
    assert excluded['forecast_unavailable_pairs'] == 5
    assert excluded['observation_download_failed_pairs'] == 2
    assert excluded['no_observation_within_tolerance_pairs'] == 4
    assert excluded['deployment_unknown_or_transition_pairs'] == 1
    assert excluded['on_land_pairs'] == 1
    assert excluded['provider_mask_missing_pairs'] == 1
    assert sum(v for k, v in excluded.items() if k.endswith('_pairs')) == 14
    assert ga.latest_report()['run_id'] == report['run_id']


@pytest.mark.parametrize('corrupt', ['raw', 'decoded'])
def test_offline_forecast_cache_checks_both_raw_and_decoded_hashes(tmp_path, monkeypatch, corrupt):
    monkeypatch.setattr(ga, 'root', lambda: tmp_path)
    folder = tmp_path/'archive'/'20260901T00'
    folder.mkdir(parents=True)
    path = folder/'f006.npz'
    raw = folder/'f006.grib2'
    raw.write_bytes(b'GRIB archive fixture')
    np.savez_compressed(path, **{key: np.ones((3, 4)) for key in ga.FIELDS})
    record = {'sha256_npz': hashlib.sha256(path.read_bytes()).hexdigest(),
              'sha256_grib': hashlib.sha256(raw.read_bytes()).hexdigest()}
    ga.atomic_json(path.with_suffix('.json'), record)
    arrays, recovered = ga.forecast_frame(None, '20260901T00', 6, offline=True)
    assert recovered == record
    assert arrays['hs'].shape == (3, 4)
    (raw if corrupt == 'raw' else path).write_bytes(b'corrupted archive')
    with pytest.raises(ValueError, match='checksum mismatch'):
        ga.forecast_frame(None, '20260901T00', 6, offline=True)
    with pytest.raises(FileNotFoundError, match='Offline frame absent'):
        ga.forecast_frame(None, '20260901T00', 24, offline=True)


@pytest.mark.parametrize('run_id', ['../../report', '/tmp/report', '20260901T000000Z/..',
                                  '20260901T000000', '', None])
def test_latest_report_rejects_untrusted_path_identifier(tmp_path, monkeypatch, run_id):
    monkeypatch.setattr(ga, 'root', lambda: tmp_path)
    ga.atomic_json(tmp_path/'latest.json', {'run_id': run_id})
    assert ga.latest_report() is None


@pytest.mark.parametrize('successful', [False, True])
def test_resume_writes_new_run_and_preserves_original_artifacts(tmp_path, monkeypatch, successful):
    monkeypatch.setattr(ga, 'root', lambda: tmp_path)
    previous_id = '20260901T120000Z'
    old = tmp_path/'runs'/previous_id
    obs_folder = old/'observations'
    obs_folder.mkdir(parents=True)
    p = ga.protocol('20260901T00', '20260901T00', '2026-09-01T06:00Z')
    ga.atomic_json(old/'protocol.json', p)
    ga.atomic_json(old/'report.json', {'run_id': previous_id, 'state': 'partial'})
    (old/'pairs.csv').write_text('old,immutable,report\n')
    metadata = b'<stations/>'
    for name in ['activestations.xml', 'stationmetadata.xml']:
        (obs_folder/name).write_bytes(metadata)
    raw = ndbc_text(['2026 09 01 06 00 2.0']).encode()
    (obs_folder/'A.txt.gz').write_bytes(gzip.compress(raw))
    station = {
        'station_id': 'A', 'name': 'A', 'lat': 0., 'lon': 0., 'state': 'downloaded',
        'sha256': hashlib.sha256(raw).hexdigest(),
        'history': [{'start': '2026-01-01', 'stop': '', 'lat': '0', 'lng': '0'}],
    }
    ga.atomic_json(obs_folder/'manifest.json', {
        'sha256': hashlib.sha256(metadata).hexdigest(),
        'history_sha256': hashlib.sha256(metadata).hexdigest(), 'stations': [station],
    })
    pointer = {'run_id': previous_id}
    ga.atomic_json(tmp_path/'latest.json', pointer)
    original_bytes = {path.relative_to(old): path.read_bytes() for path in old.rglob('*')
                      if path.is_file()}
    arrays = {key: np.ones((3, 4))*3 for key in ga.FIELDS}

    def no_network(request):
        pytest.fail('Offline replay attempted network access')

    original_client = httpx.Client
    monkeypatch.setattr(ga.httpx, 'Client', lambda **kw: original_client(
        transport=httpx.MockTransport(no_network), **kw))
    monkeypatch.setattr(ga, 'land_points', lambda lats, lons: np.array([False]))
    if successful:
        monkeypatch.setattr(ga, 'forecast_frame', lambda client, cycle, lead, offline=False: (
            arrays, {'cycle': cycle, 'lead_h': lead, 'sha256_grib': 'test-source'}))
        report = ga.run(resume=previous_id, offline=True)
        assert report['run_id'] != previous_id
        assert report['overall']['n'] == 1
        assert ga.read_json(tmp_path/'latest.json')['run_id'] == report['run_id']
        assert report['protocol'] == p
    else:
        with pytest.raises(RuntimeError, match='All forecast frames unavailable'):
            ga.run(resume=previous_id, offline=True)
        assert ga.read_json(tmp_path/'latest.json') == pointer
    assert {path.relative_to(old): path.read_bytes() for path in old.rglob('*')
            if path.is_file()} == original_bytes
