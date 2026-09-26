"""Reproducible, retrospective global-provider Hs observational comparison.

Run from project root: python -m poseidon.validation.global_audit --help
Separate store, no regional error_sample writes, model tuning or service restart.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import gzip
import hashlib
import json
import os
import re
import shutil
import time
from collections import Counter
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from poseidon.core.config import settings
from poseidon.core.types import Cycle
from poseidon.ingest.global_wave import (SOURCE, FIELDS, atomic_json, read_json,
                                        parameters, decode, utcnow)
from poseidon.ingest.adapters.gfswave import FILTER_URL
from poseidon.twin.global_forecast import spatial, corners
from poseidon.twin.global_coast import land_points
from poseidon.validation.global_observations import (
    acquire_observations, nearest_observation, deployment, parse_hs, STATIONS_URL, HISTORY_URL)
from poseidon.validation.global_metrics import summarize, region

LEADS = (6,24,48,72,120)
PAIR_COLUMNS = ('station_id','name','lat','lon','region','cycle','lead_h','valid_time',
                'observation_time','time_offset_minutes','forecast_hs_m','observed_hs_m',
                'error_m','wet_weight','deployment_start','deployment_stop','hull',
                'observation_sha256','forecast_sha256_grib')


def root():
    return settings.data_root/'validation'/'global'


def protocol(start, end, cutoff):
    a, b = pd.to_datetime(start,format='%Y%m%dT%H',utc=True), pd.to_datetime(end,format='%Y%m%dT%H',utc=True)
    c = pd.Timestamp(cutoff)
    if c.tzinfo is None:
        raise ValueError('cutoff must include UTC offset')
    c = c.tz_convert('UTC')
    if a > b or a.hour != 0 or b.hour != 0 or (b-a).days > 31 or c > pd.Timestamp.now(tz='UTC'):
        raise ValueError('Use an ordered 00Z window of at most 32 cycles and a nonfuture cutoff')
    return {'id':'global-hs-obs-v1', 'registered_at':utcnow(),
            'cycle_start':start, 'cycle_end':end,
            'cycles':[t.strftime('%Y%m%dT%H') for t in pd.date_range(a,b,freq='D')],
            'leads_h':list(LEADS), 'cutoff_utc':c.isoformat(), 'tolerance_minutes':30,
            'variable':'HTSGW versus NDBC WVHT [m], raw unchanged forecast; f0 excluded',
            'station_selection':'All active met=y,type=buoy stations, all NDBC-distributed providers',
            'coordinates':'Historical deployment coordinates; ambiguous/missing/transition-day excluded',
            'time_matching':'Nearest released nominal UTC row within +/-30min; ties earlier; no future rows',
            'observation_qc':'Provider automatic-QC release; finite 0<=WVHT<=40m gross screen; conflicting duplicate times excluded',
            'spatial':'Serving sea-normalized bilinear interpolation; wet weight >=0.05; Natural Earth land screen',
            'aggregation':'Paired-sample weighted, observation reuse counted separately; station-balanced mean RMSE also reported',
            'uncertainty':'2000 cycle-cluster replicates, seed 240911, minimum 5 cycles; exploratory sensitivity only',
            'hs_bands_m':[0,2,4,6], 'geography':'Disjoint coordinate sectors, not exact basin boundaries',
            'independence':'External observations, no Poseidon fitting. Upstream assimilation/development independence not established.',
            'promotion':'Descriptive pilot only. No industrial/global accuracy approval threshold is inferred from these data.'}


def targets(p):
    cutoff = pd.Timestamp(p['cutoff_utc'])
    return [(cycle,h) for cycle in p['cycles'] for h in p['leads_h']
            if pd.to_datetime(cycle,format='%Y%m%dT%H',utc=True)+pd.Timedelta(hours=h) <= cutoff]


def forecast_frame(client, cycle_label, lead, offline=False):
    """Preserve original GRIB and derived arrays; verify reusable cache hashes."""
    dest = root()/'archive'/cycle_label
    dest.mkdir(parents=True,exist_ok=True)
    path = dest/f'f{lead:03d}.npz'
    record = read_json(path.with_suffix('.json'))
    raw_path = path.with_suffix('.grib2')
    if record and path.exists() and raw_path.exists():
        if (hashlib.sha256(path.read_bytes()).hexdigest()==record.get('sha256_npz')
                and hashlib.sha256(raw_path.read_bytes()).hexdigest()==record.get('sha256_grib')):
            with np.load(path,allow_pickle=False) as data:
                return {k:data[k] for k in FIELDS}, record
        raise ValueError('Validation forecast cache checksum mismatch; preserved for inspection')
    if offline:
        raise FileNotFoundError(f'Offline frame absent: {cycle_label}+{lead}')
    cycle = Cycle(pd.to_datetime(cycle_label,format='%Y%m%dT%H',utc=True).to_pydatetime())
    for attempt in range(3):
        try:
            response = client.get(FILTER_URL,params=parameters(cycle,lead))
            response.raise_for_status()
            arrays = decode(response.content,cycle,lead)
            raw_path.write_bytes(response.content)
            with path.with_suffix('.npz.tmp').open('wb') as stream:
                np.savez_compressed(stream,**arrays)
            os.replace(path.with_suffix('.npz.tmp'),path)
            record = {'cycle':cycle_label, 'lead_h':lead, 'retrieved_at':utcnow(),
                      'url':str(response.url), 'sha256_grib':hashlib.sha256(response.content).hexdigest(),
                      'sha256_npz':hashlib.sha256(path.read_bytes()).hexdigest()}
            atomic_json(path.with_suffix('.json'),record)
            time.sleep(.5)
            return arrays,record
        except (httpx.HTTPError, ValueError) as exc:
            if attempt==2:
                raise exc
            time.sleep(2**attempt)


def read_observations(folder):
    m = read_json(folder/'manifest.json')
    if not m:
        raise FileNotFoundError('Observation snapshot manifest missing')
    for name,key in [('activestations.xml','sha256'),('stationmetadata.xml','history_sha256')]:
        if hashlib.sha256((folder/name).read_bytes()).hexdigest()!=m[key]:
            raise ValueError(f'Observation metadata checksum mismatch: {name}')
    observations = {}
    for s in m['stations']:
        if s['state']!='downloaded':
            observations[s['station_id']] = pd.DataFrame(columns=['ts','hs'])
            continue
        raw = gzip.decompress((folder/f"{s['station_id']}.txt.gz").read_bytes())
        if hashlib.sha256(raw).hexdigest()!=s['sha256']:
            raise ValueError('Observation checksum mismatch: '+s['station_id'])
        observations[s['station_id']] = parse_hs(raw.decode())[0]
    return m,observations


def code_fingerprints():
    files = [Path(__file__),Path(__file__).with_name('global_observations.py'),
             Path(__file__).with_name('global_metrics.py'),
             Path(__file__).parents[1]/'twin'/'global_forecast.py',
             Path(__file__).parents[1]/'twin'/'global_coast.py',
             Path(__file__).parents[1]/'ingest'/'global_wave.py']
    return {str(f.relative_to(Path(__file__).parents[2])):hashlib.sha256(f.read_bytes()).hexdigest()
            for f in files}


def run(p=None, resume=None, offline=False):
    code_hashes = code_fingerprints()
    root().mkdir(parents=True,exist_ok=True)
    with (root()/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if resume and not re.fullmatch(r'\d{8}T\d{6}Z',resume):
            raise ValueError('Invalid validation run ID')
        run_id = pd.Timestamp.now(tz='UTC').strftime('%Y%m%dT%H%M%SZ')
        folder = root()/'runs'/run_id
        folder.mkdir(parents=True,exist_ok=False)
        if resume:
            previous = root()/'runs'/resume
            p = read_json(previous/'protocol.json')
            if not p:
                raise ValueError('No frozen protocol for requested run')
            # New immutable result using the identical frozen observation snapshot.
            # A failed replay cannot mutate CSV/report files already served to users.
            if (previous/'observations'/'manifest.json').exists():
                shutil.copytree(previous/'observations',folder/'observations')
        else:
            if offline:
                raise ValueError('--offline requires --resume')
        atomic_json(folder/'protocol.json',p)  # Before any observations or forecast errors.
        status = {'run_id':run_id, 'state':'observations', 'updated_at':utcnow()}
        atomic_json(root()/'status.json',status)
        try:
            obs_folder = folder/'observations'
            if (obs_folder/'manifest.json').exists():
                obs_manifest, observations = read_observations(obs_folder)
            elif offline:
                raise FileNotFoundError('Offline observation snapshot missing')
            else:
                obs_manifest, observations = acquire_observations(obs_folder)
            stations = obs_manifest['stations']
            if stations and not any(s['state']=='downloaded' for s in stations):
                raise RuntimeError('All observation downloads failed; prior published report preserved')
            print(f'Observations: {len(stations)} candidates, '+
                  f'{sum(not d.empty for d in observations.values())} with Hs',flush=True)
            pairs, source_records, failures = [], [], []
            exclusions = Counter()
            selected = targets(p)
            exclusions['forecast_targets_not_yet_valid'] = len(p['cycles'])*len(p['leads_h'])-len(selected)
            cutoff = pd.Timestamp(p['cutoff_utc'])
            with httpx.Client(follow_redirects=True,timeout=90) as client:
                for i,(cycle,lead) in enumerate(selected):
                    status.update(state='forecasts',completed=i,total=len(selected),cycle=cycle,lead_h=lead,updated_at=utcnow())
                    atomic_json(root()/'status.json',status)
                    try:
                        data,record = forecast_frame(client,cycle,lead,offline=offline)
                    except (httpx.HTTPError,ValueError,OSError,KeyError) as exc:
                        failures.append({'cycle':cycle,'lead_h':lead,'error':str(exc)[:300]})
                        exclusions['forecast_unavailable_pairs'] += len(stations)
                        print(f'Unavailable {cycle}+{lead}: {exc}',flush=True)
                        continue
                    source_records.append(record)
                    when = pd.to_datetime(cycle,format='%Y%m%dT%H',utc=True)+pd.Timedelta(hours=lead)
                    for station in stations:
                        sid = station['station_id']
                        if station['state']!='downloaded':
                            exclusions['observation_download_failed_pairs'] += 1
                            continue
                        obs = nearest_observation(observations[sid],when,cutoff,p['tolerance_minutes'])
                        if obs is None:
                            exclusions['no_observation_within_tolerance_pairs'] += 1
                            continue
                        loc = deployment(station,obs['observation_time'])
                        if loc is None:
                            exclusions['deployment_unknown_or_transition_pairs'] += 1
                            continue
                        land = land_points([loc['lat']],[loc['lon']])
                        if land is None or bool(land[0]):
                            exclusions['land_screen_unavailable_pairs' if land is None else 'on_land_pairs'] += 1
                            continue
                        value = spatial(data,loc['lat'],loc['lon'])
                        if value is None:
                            exclusions['provider_mask_missing_pairs'] += 1
                            continue
                        jj,ii,w = corners(loc['lat'],loc['lon'],data['hs'].shape)
                        hs = data['hs'][jj,ii]
                        wet_weight = float(np.sum(w*(np.isfinite(hs)&(hs>=0))))
                        pairs.append({'station_id':sid,'name':station['name'],**loc,**obs,
                                      'region':region(loc['lat'],loc['lon']), 'cycle':cycle,'lead_h':lead,
                                      'valid_time':when.isoformat(),'forecast_hs_m':value['hs'],
                                      'error_m':value['hs']-obs['observed_hs_m'],'wet_weight':wet_weight,
                                      'observation_sha256':station['sha256'],
                                      'forecast_sha256_grib':record['sha256_grib']})
                    print(f'{i+1}/{len(selected)} {cycle}+{lead:03d} paired cumulative={len(pairs)}',flush=True)
            if selected and not source_records:
                raise RuntimeError('All forecast frames unavailable; prior published report preserved')
            summary = summarize(pairs,stations,p['leads_h'])
            with (folder/'pairs.csv').open('w',newline='') as stream:
                writer = csv.DictWriter(stream,fieldnames=PAIR_COLUMNS)
                writer.writeheader()
                writer.writerows(pairs)
            qc = Counter()
            for s in stations:
                qc.update(s.get('qc_counts',{}))
            report = {'schema_version':'global-validation-1.0', 'state':'partial' if pairs else 'unavailable',
                      'run_id':run_id, 'replayed_from':resume, 'generated_at':utcnow(), 'model':SOURCE, 'variable':'hs',
                      'protocol':p, **summary,
                      'coverage':{'global_ready':False,
                          'valid_start':min((r['valid_time'] for r in pairs),default=None),
                          'valid_end':max((r['valid_time'] for r in pairs),default=None),
                          'missing_regions':[r['label'] for r in summary['by_region'] if not r['n']],
                          'unscored_leads_h':[h for h in range(6,385,6) if not any(r['lead_h']==h for r in pairs)]},
                      'acquisition':{'stations_discovered':len(stations),
                          'stations_downloaded':sum(s['state']=='downloaded' for s in stations),
                          'stations_with_hs':sum(not d.empty for d in observations.values()),
                          'forecast_frames_requested':len(selected),'forecast_frames_available':len(source_records),
                          'candidate_pairs':len(selected)*len(stations),'exclusions':dict(exclusions),
                          'observation_row_qc':dict(qc), 'forecast_failures':failures},
                      'source_records':source_records,
                      'observation_sources':{'catalog':STATIONS_URL,'history':HISTORY_URL,
                          'catalog_sha256':obs_manifest['sha256'],'history_sha256':obs_manifest['history_sha256']},
                      'artifact_sha256':{'pairs.csv':hashlib.sha256((folder/'pairs.csv').read_bytes()).hexdigest(),
                          'observations/manifest.json':hashlib.sha256((obs_folder/'manifest.json').read_bytes()).hexdigest(),
                          'protocol.json':hashlib.sha256((folder/'protocol.json').read_bytes()).hexdigest()},
                      'code_sha256':code_hashes,
                      'downloads':{'report':f'/v1/global/validation/report.json?run_id={run_id}',
                                   'pairs':f'/v1/global/validation/pairs.csv?run_id={run_id}'},
                      'caveats':[
                          '일부 부이의 단기간 Hs 관측 대조입니다. 전 지구 정확도나 산업 운항 적합성을 승인하지 않습니다.',
                          'NDBC 자동 품질검사 공개 자료이며 사후 확정 관측이 아닙니다. 명목 행 시각은 파랑 취득 구간과 다를 수 있습니다.',
                          '현재 활동 중인 부이를 선택하므로 이미 철거된 관측소는 빠질 수 있습니다. 좌표는 관측 당시 배치 이력을 사용합니다.',
                          '독립 관측 대조는 Poseidon 보정·학습에 사용하지 않은 실측과의 비교를 뜻합니다. 기관 독립·NOAA 상류 동화 및 개발 표본 독립성은 입증되지 않았습니다.',
                          '같은 관측이 여러 예보 사이클에서 반복 채점됩니다. 사이클 부트스트랩 구간은 단기 민감도이며 인접 사이클의 같은 기상을 독립 사건으로 보장하지 않습니다.',
                          '관측소 좌표의 표본 분포는 주변 해역의 정확도 보증 범위가 아닙니다. 해역 구분은 경위도 기반 구역입니다.',
                          '주기·파향·바람·해류·선박 응답 및 16일 전체 리드는 채점하지 않았습니다. 무표본 해역·계절·극한파랑은 별도 검증이 필요합니다.']}
            if code_hashes != code_fingerprints():
                raise RuntimeError('Validation code changed during execution; recompute before publication')
            atomic_json(folder/'report.json',report)
            # Atomic pointer after every artifact is complete. Failed runs preserve prior report.
            atomic_json(root()/'latest.json',{'run_id':run_id,'generated_at':report['generated_at']})
            atomic_json(root()/'status.json',{**status,'state':'ready','completed':len(selected),'updated_at':utcnow()})
            print(json.dumps({'run_id':run_id,'overall':report['overall'],'missing_regions':report['coverage']['missing_regions']},ensure_ascii=False),flush=True)
            return report
        except Exception as exc:
            atomic_json(root()/'status.json',{**status,'state':'failed','error':str(exc)[:400],'updated_at':utcnow()})
            raise


def latest_report():
    latest = read_json(root()/'latest.json')
    rid = latest.get('run_id','') if isinstance(latest,dict) else ''
    if not isinstance(rid,str) or not re.fullmatch(r'\d{8}T\d{6}Z',rid):
        return None
    return read_json(root()/'runs'/rid/'report.json')


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    end = pd.Timestamp.now(tz='UTC').normalize()-pd.Timedelta(days=1)
    parser.add_argument('--start',default=(end-pd.Timedelta(days=7)).strftime('%Y%m%dT00'))
    parser.add_argument('--end',default=end.strftime('%Y%m%dT00'))
    parser.add_argument('--cutoff',default=(pd.Timestamp.now(tz='UTC').floor('h')-pd.Timedelta(hours=1)).isoformat())
    parser.add_argument('--resume',help='Reuse frozen observation snapshot and protocol for a prior run ID')
    parser.add_argument('--offline',action='store_true',help='Recompute using checksummed archives only')
    args = parser.parse_args()
    run(None if args.resume else protocol(args.start,args.end,args.cutoff),args.resume,args.offline)
