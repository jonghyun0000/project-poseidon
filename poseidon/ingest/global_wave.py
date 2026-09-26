"""Whole-world NOAA GFS-Wave cache, independent of the frozen regional engine.

NCEP product inventory: https://www.nco.ncep.noaa.gov/pmb/products/wave/
Keep native 0.25 degree fields, every 6 h through 384 h. PERPW/DIRPW retain
their GRIB primary-wave definitions; they are not relabelled Tp/peak direction.
Publish only complete cycles; retain the previous cycle during refresh/failure.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np

from poseidon.core.config import settings
from poseidon.core.types import Cycle
from poseidon.ingest.adapters.gfswave import FILTER_URL, PUB_URL, _decode_step

STEPS = tuple(range(0, 385, 6))
SOURCE = 'NOAA GFS-Wave / WAVEWATCH III global 0.25°'
FIELDS = {'hs': 'swh', 'primary_period': 'perpw', 'primary_direction': 'dirpw',
          'wind_u': 'u', 'wind_v': 'v'}
_thread_lock = threading.Lock()
_thread = None
_last_check = 0.0


def root():
    return settings.data_root / 'global' / 'gfswave'


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    os.replace(temporary, path)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def parameters(cycle, step):
    return {'dir': f'/gfs.{cycle.ymd}/{cycle.hh}/wave/gridded',
            'file': f'gfswave.t{cycle.hh}z.global.0p25.f{step:03d}.grib2',
            'lev_surface': 'on', **{f'var_{v}': 'on' for v in
                                   ['HTSGW', 'PERPW', 'DIRPW', 'UGRD', 'VGRD']}}


def discover(client):
    cycle = Cycle.latest()
    for _ in range(5):
        url = (f'{PUB_URL}/gfs.{cycle.ymd}/{cycle.hh}/wave/gridded/'
               f'gfswave.t{cycle.hh}z.global.0p25.f384.grib2.idx')
        try:
            if client.head(url, timeout=20).status_code == 200:
                return cycle
        except httpx.HTTPError:
            pass
        cycle = cycle.previous()
    raise RuntimeError('NOAA에서 완전한 전 지구 예보 사이클을 확인하지 못했습니다.')


def decode(raw, cycle, step):
    if raw[:4] != b'GRIB':
        raise ValueError('NOAA 응답이 GRIB 자료가 아닙니다.')
    ds = _decode_step(raw).sortby('latitude')
    origin = np.datetime64(cycle.t0.replace(tzinfo=None))
    expected = origin + np.timedelta64(step, 'h')
    if np.datetime64(ds.time.values) != origin or ds.step.values != np.timedelta64(step,'h'):
        raise ValueError('전 지구 자료의 초기시각 또는 리드가 요청과 다릅니다.')
    if np.datetime64(ds.valid_time.values) != expected:
        raise ValueError('전 지구 자료의 유효시각이 요청과 다릅니다.')
    lat, lon = np.asarray(ds.latitude), np.asarray(ds.longitude)
    if (lat.shape != (721,) or lon.shape != (1440,)
            or not np.allclose(lat, np.linspace(-90, 90, 721), atol=5e-5, rtol=0)
            or not np.allclose(lon, np.arange(1440)*.25, atol=5e-5, rtol=0)):
        raise ValueError('전 지구 0.25도 격자가 아닙니다.')
    arrays = {k: ds[v].transpose('latitude', 'longitude').values.astype('float32')
              for k, v in FIELDS.items()}
    if not all(np.isfinite(v).any() for v in arrays.values()):
        raise ValueError('전 지구 필수 변수에 유효한 값이 없습니다.')
    return arrays


def refresh():
    """Process lock also prevents a CLI/API double download; resumable per step."""
    folder = root()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'refresh.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        state = {'state': 'checking', 'updated_at': utcnow(), 'completed': 0, 'total': len(STEPS)}
        atomic_json(folder / 'status.json', state)
        try:
            with httpx.Client(follow_redirects=True, timeout=90) as client:
                cycle = discover(client)
                latest = read_json(folder / 'latest.json')
                if latest and latest['cycle'] >= cycle.label:
                    atomic_json(folder / 'status.json', {**state, 'state': 'ready', 'cycle': latest['cycle'],
                                                        'completed': len(STEPS), 'updated_at': utcnow()})
                    return
                dest = folder / cycle.label
                dest.mkdir(exist_ok=True)
                records = []
                for i, step in enumerate(STEPS):
                    target = dest / f'f{step:03d}.npz'
                    record = read_json(target.with_suffix('.json'))
                    if not target.exists() or not record:
                        failure = None
                        for attempt in range(3):
                            try:
                                response = client.get(FILTER_URL, params=parameters(cycle, step))
                                response.raise_for_status()
                                arrays = decode(response.content, cycle, step)
                                with target.with_suffix('.npz.tmp').open('wb') as stream:
                                    np.savez_compressed(stream, **arrays)
                                os.replace(target.with_suffix('.npz.tmp'), target)
                                record = {'lead_h': step, 'retrieved_at': utcnow(),
                                          'sha256_grib': hashlib.sha256(response.content).hexdigest(),
                                          'request': parameters(cycle, step)}
                                atomic_json(target.with_suffix('.json'), record)
                                failure = None
                                break
                            except (httpx.HTTPError, ValueError, OSError) as exc:
                                failure = exc
                                time.sleep(2**attempt)
                        if failure:
                            raise failure
                        time.sleep(.5)  # Sequential requests with a pause, per NOMADS guidance.
                    records.append(record)
                    state.update(state='downloading', cycle=cycle.label, completed=i+1, updated_at=utcnow())
                    atomic_json(folder / 'status.json', state)
                manifest = {'cycle': cycle.label, 'source': SOURCE, 'source_id': 'noaa-gfswave-global-0p25',
                            'source_url': FILTER_URL, 'retrieved_at': utcnow(), 'leads_h': list(STEPS),
                            'resolution_deg': .25, 'time_interval_h': 6, 'records': records,
                            'definitions': {'hs': 'HTSGW significant combined wind-wave and swell height [m]',
                                            'primary_period': 'PERPW primary wave mean period [s]',
                                            'primary_direction': 'DIRPW primary wave direction [degree true]',
                                            'wind': 'UGRD/VGRD surface forcing wind [m/s]; GRIB level=surface'}}
                atomic_json(dest / 'manifest.json', manifest)
                atomic_json(folder / 'latest.json', manifest)
                atomic_json(folder / 'status.json', {**state, 'state': 'ready', 'updated_at': utcnow()})
        except Exception as exc:
            atomic_json(folder / 'status.json', {**state, 'state': 'failed', 'error': str(exc)[:500],
                                                'updated_at': utcnow()})
            raise


def ensure_refresh():
    """A live console requests refresh checks; network work never blocks its GET."""
    global _thread, _last_check
    with _thread_lock:
        if (_thread and _thread.is_alive()) or time.monotonic()-_last_check < 900:
            return
        _last_check = time.monotonic()
        def run():
            try:
                refresh()
            except Exception:
                pass  # Failure is recorded atomically in status.json and exposed by API.
        _thread = threading.Thread(target=run, daemon=True, name='global-wave-refresh')
        _thread.start()


if __name__ == '__main__':
    refresh()
    print(json.dumps(read_json(root() / 'status.json'), ensure_ascii=False))
