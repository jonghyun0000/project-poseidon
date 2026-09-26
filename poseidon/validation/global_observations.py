"""Archive NDBC's released realtime observations; never mark them research-QC.

https://www.ndbc.noaa.gov/faq/realtime.shtml (MM, UTC, automatic QC)
https://www.ndbc.noaa.gov/faq/acq.shtml (nominal row time != wave acquisition time)
"""
from __future__ import annotations

import gzip
import hashlib
import io
import re
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx
import numpy as np
import pandas as pd

from poseidon.ingest.global_wave import atomic_json, utcnow

STATIONS_URL = 'https://www.ndbc.noaa.gov/activestations.xml'
HISTORY_URL = 'https://www.ndbc.noaa.gov/metadata/stationmetadata.xml'
OBS_URL = 'https://www.ndbc.noaa.gov/data/realtime2/{station_id}.txt'


def parse_hs(text):
    """Keep only finite released WVHT, retaining conflicting-time exclusions."""
    counts = Counter()
    lines = text.splitlines()
    if not lines or not lines[0].startswith('#YY'):
        raise ValueError('NDBC realtime2 header is missing')
    names = lines[0].lstrip('#').split()
    if 'WVHT' not in names:
        raise ValueError('WVHT column is missing')
    raw = pd.read_csv(io.StringIO(text), sep=r'\s+', comment='#', names=names,
                      dtype=str, keep_default_na=False)
    counts['rows'] = len(raw)
    ts = pd.to_datetime(raw[['YY','MM','DD','hh','mm']].rename(columns={
        'YY':'year','MM':'month','DD':'day','hh':'hour','mm':'minute'}), utc=True, errors='coerce')
    hs = pd.to_numeric(raw.WVHT, errors='coerce')
    counts['missing_hs'] = int((~np.isfinite(hs)).sum())
    counts['invalid_time'] = int(ts.isna().sum())
    # Broad gross-value screen declared before errors are inspected. No spike or
    # stuck-value filtering: rounded repeated calm values need not be faulty.
    good = ts.notna() & np.isfinite(hs) & hs.between(0, 40)
    counts['outside_gross_range'] = int((np.isfinite(hs) & ~hs.between(0,40)).sum())
    data = pd.DataFrame({'ts':ts[good], 'hs':hs[good]})
    conflicts = data.groupby('ts').hs.nunique()
    conflict_times = conflicts[conflicts > 1].index
    counts['conflicting_timestamp_rows'] = int(data.ts.isin(conflict_times).sum())
    data = data[~data.ts.isin(conflict_times)]
    counts['identical_duplicate_rows'] = int(data.duplicated('ts').sum())
    data = data.drop_duplicates('ts').sort_values('ts').reset_index(drop=True)
    counts['usable_rows'] = len(data)
    return data, dict(counts)


def nearest_observation(data, valid_time, cutoff, tolerance_minutes=30):
    """One nominal observation per forecast target; earlier wins equal distance."""
    if data.empty:
        return None
    valid_time, cutoff = pd.Timestamp(valid_time), pd.Timestamp(cutoff)
    i = int(data.ts.searchsorted(valid_time))
    choices = [j for j in (i-1,i) if 0 <= j < len(data) and data.ts.iloc[j] <= cutoff]
    if not choices:
        return None
    j = min(choices, key=lambda j: (abs(data.ts.iloc[j]-valid_time), data.ts.iloc[j]))
    row = data.iloc[j]
    offset = (row.ts-valid_time).total_seconds()/60
    if abs(offset) > tolerance_minutes:
        return None
    return {'observation_time':row.ts.isoformat(), 'observed_hs_m':float(row.hs),
            'time_offset_minutes':float(offset)}


def acquire_observations(folder):
    """Bounded downloads; a new run snapshots every station without silent omission."""
    folder.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=40) as client:
        response = client.get(STATIONS_URL)
        response.raise_for_status()
        (folder/'activestations.xml').write_bytes(response.content)
        catalog = ET.fromstring(response.content)
        history_response = client.get(HISTORY_URL)
        history_response.raise_for_status()
        (folder/'stationmetadata.xml').write_bytes(history_response.content)
        histories = {s.get('id','').lower(): [h.attrib for h in s.findall('history')]
                     for s in ET.fromstring(history_response.content)}
        stations = []
        for item in catalog:
            a = item.attrib
            if a.get('met') != 'y' or a.get('type') != 'buoy':
                continue
            sid = a.get('id','')
            if not re.fullmatch(r'[a-zA-Z0-9]{3,12}', sid):
                continue
            lat, lon = float(a['lat']), float(a['lon'])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            stations.append({**a, 'station_id':sid, 'lat':lat, 'lon':lon,
                             'history':histories.get(sid.lower(),[])})
        def fetch(station):
            url = OBS_URL.format(**station)
            record = {**station, 'url':url, 'retrieved_at':utcnow()}
            try:
                r = client.get(url)
                r.raise_for_status()
                raw = r.content
                (folder/f"{station['station_id']}.txt.gz").write_bytes(gzip.compress(raw, mtime=0))
                record.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), state='downloaded')
                data, counts = parse_hs(r.text)
                record['qc_counts'] = counts
                return record, data
            except (httpx.HTTPError, ValueError, OSError) as exc:
                record.update(state='failed', error=str(exc)[:240])
                return record, pd.DataFrame(columns=['ts','hs'])
        results = list(ThreadPoolExecutor(max_workers=6).map(fetch, sorted(stations, key=lambda s:s['station_id'])))
    records, observations = [], {}
    for record, data in results:
        records.append(record)
        observations[record['station_id']] = data
    manifest = {'url':STATIONS_URL, 'retrieved_at':utcnow(), 'catalog_created':catalog.get('created'),
                'sha256':hashlib.sha256(response.content).hexdigest(),
                'history_url':HISTORY_URL,
                'history_sha256':hashlib.sha256(history_response.content).hexdigest(),
                'selection':'activestations met=y AND type=buoy; all providers, no error-based selection',
                'stations':records}
    atomic_json(folder/'manifest.json', manifest)
    return manifest, observations


def deployment(station, when):
    """Date-only metadata: exclude transition dates, never assume current location."""
    timestamp = pd.Timestamp(when)
    if timestamp.tzinfo is None:
        raise ValueError('Deployment lookup requires timezone-aware time')
    day = timestamp.tz_convert('UTC').date().isoformat()
    candidates = []
    for h in station.get('history',[]):
        if not h.get('start') or not (h['start'] < day and (not h.get('stop') or day < h['stop'])):
            continue
        try:
            lat, lon = float(h['lat']), float(h['lng'])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                candidates.append({'lat':lat, 'lon':lon, 'deployment_start':h['start'],
                                   'deployment_stop':h.get('stop'), 'hull':h.get('hull')})
        except (KeyError, ValueError):
            continue
    return candidates[0] if len(candidates)==1 else None
