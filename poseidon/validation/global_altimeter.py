"""Frozen, separate Sentinel-3A open-ocean Hs comparison; never model fitting.

Only the existing, checksummed buoy-audit forecast archive is read. The RADS
sensor variable swh_ku is used, never the model wave fields in the same files.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import fcntl
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import struct

import httpx
import numpy as np
import pandas as pd
from geographiclib.geodesic import Geodesic
import xarray as xr

from poseidon.core.config import settings
from poseidon.ingest.global_wave import atomic_json, read_json, utcnow, FIELDS, SOURCE
from poseidon.twin.global_forecast import spatial
from poseidon.validation import global_metrics as gm

BASE = 'https://www.star.nesdis.noaa.gov/pub/sod/lsa/rads/data/'
DIRECTORIES = ('3a/a/c143/', '3a/a/c144/')
FLAGS = ('sar_mode attitude_bad continental_ice alt_iono_bad alt_land alt_non_ocean '
         'rad_land alt_rain_or_ice rad_rain_or_ice tb2_bad tb3_bad range_suspect '
         'swh_suspect backscatter_suspect not_used orbit_degraded').split()
STRICT_MASK = 0xBFFE  # All named bad conditions; allow SAR mode and unused bit.
GEOD = Geodesic.WGS84
RUN_ID = re.compile(r'\d{8}T\d{6}Z')
PAIR_COLUMNS = ('block_id','track_id','mission','mission_phase','satellite_cycle','pass_number',
                'lat','lon','region','n_samples','cycle','lead_h','valid_time',
                'observation_time','observation_start','observation_end','time_offset_minutes',
                'max_abs_time_offset_minutes','observed_hs_m','forecast_hs_m','error_m',
                'observation_sha256','forecast_sha256_grib')


def root():
    return settings.data_root/'validation'/'global_altimeter'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def latest_report():
    latest = read_json(root()/'latest.json')
    run_id = latest.get('run_id','') if latest else ''
    if not isinstance(run_id,str) or not RUN_ID.fullmatch(run_id):
        return None
    return read_json(root()/'runs'/run_id/'report.json')


def nc_attributes(data):
    """Parse only the bounded classic-NetCDF global header, without arrays."""
    if data[:4] not in (b'CDF\x01', b'CDF\x02'):
        raise ValueError('Expected classic NetCDF RADS header')
    offset = 4

    def number():
        nonlocal offset
        if offset+4 > len(data):
            raise ValueError('Truncated NetCDF header')
        result = struct.unpack_from('>I',data,offset)[0]
        offset += 4
        return result

    def string():
        nonlocal offset
        n = number()
        if n > len(data)-offset:
            raise ValueError('Truncated NetCDF header string')
        result = data[offset:offset+n].decode('utf-8')
        offset += (n+3)//4*4
        return result

    number()  # numrecs
    tag, n = number(),number()
    if tag not in (0,10) or n > 10000 or (tag==0 and n):
        raise ValueError('Unexpected NetCDF dimensions')
    for _ in range(n):
        string(); number()
    tag, n = number(),number()
    if tag not in (0,12) or n > 10000 or (tag==0 and n):
        raise ValueError('Unexpected NetCDF attributes')
    attributes = {}
    formats = {1:('b',1),2:('c',1),3:('h',2),4:('i',4),5:('f',4),6:('d',8)}
    for _ in range(n):
        name, kind, count = string(),number(),number()
        if kind not in formats:
            raise ValueError('Unknown NetCDF attribute type')
        fmt,size = formats[kind]
        length = count*size
        if length > len(data)-offset:
            raise ValueError('Global attributes exceed bounded header')
        raw = data[offset:offset+length]
        if kind==2:
            value = raw.decode('utf-8')
        else:
            values = struct.unpack('>'+fmt*count,raw)
            value = values[0] if count==1 else list(values)
        attributes[name] = value
        offset += (length+3)//4*4
    return attributes


def header_times(attributes):
    if attributes.get('mission_name')!='SNTNL-3A' or attributes.get('mission_phase')!='a':
        raise ValueError('Unexpected RADS mission or phase')
    if attributes.get('source')!='radar altimeter' or attributes.get('featureType')!='trajectory':
        raise ValueError('Expected observed radar-altimeter trajectory')
    start,end = (pd.Timestamp(attributes[k],tz='UTC')
                 for k in ('first_meas_time','last_meas_time'))
    if pd.isna(start) or pd.isna(end) or start > end:
        raise ValueError('Invalid RADS measurement times')
    return start,end


def check_identity(attributes,name,expected_cycle=None):
    match = re.fullmatch(r'3ap(\d{4})c(\d{3})\.nc',name)
    if (not match or attributes.get('filename')!=name
            or attributes.get('pass_number')!=int(match[1])
            or attributes.get('cycle_number')!=int(match[2])
            or int(match[2]) not in (143,144)
            or (expected_cycle is not None and int(match[2])!=expected_cycle)):
        raise ValueError('RADS filename, pass, cycle or directory identity mismatch')


def overlaps(start,end,valid_times,tolerance_minutes=30):
    delta = pd.Timedelta(minutes=tolerance_minutes)
    return any(start <= when+delta and end >= when-delta for when in valid_times)


def frame_records():
    archive = settings.data_root/'validation'/'global'
    latest = read_json(archive/'latest.json')
    if not latest or not isinstance(latest.get('run_id'),str) or not RUN_ID.fullmatch(latest['run_id']):
        raise ValueError('Completed buoy-audit forecast archive required')
    report = read_json(archive/'runs'/latest['run_id']/'report.json')
    records = report['source_records']
    expected = {(t.strftime('%Y%m%dT%H'),h)
                for t in pd.date_range('2026-09-03','2026-09-10',freq='D',tz='UTC')
                for h in (6,24,48,72,120)
                if t+pd.Timedelta(hours=h)<=pd.Timestamp('2026-09-11T16:00:00Z')}
    if {(r['cycle'],r['lead_h']) for r in records}!=expected or len(records)!=len(expected):
        raise ValueError('Expected exactly the frozen 33 archived forecast targets')
    for r in records:
        folder = archive/'archive'/r['cycle']
        for extension,key in (('npz','sha256_npz'),('grib2','sha256_grib')):
            if digest(folder/f"f{r['lead_h']:03d}.{extension}")!=r[key]:
                raise ValueError('Forecast archive hash mismatch')
    return latest['run_id'],records


def protocol(buoy_run,records):
    return {'id':'sentinel3a-open-ocean-hs-v1','registered_at':utcnow(),
            'buoy_archive_run':buoy_run,'forecast_records':records,
            'cycle_start':'20260903T00','cycle_end':'20260910T00',
            'cutoff_utc':'2026-09-11T16:00:00Z','tolerance_minutes':30,
            'directories':[BASE+p for p in DIRECTORIES],
            'variable':'Observed RADS swh_ku [m] versus raw NOAA HTSGW [m]; no model wave fields from RADS',
            'mission':'Sentinel-3A, phase a, cycles 143/144; f0 forecast excluded',
            'selection':'All directory entries are header-probed; download every pass overlapping any fixed forecast-valid-time window',
            'qc':{'mask':STRICT_MASK,'allowed_bits':['sar_mode','not_used'],
                  'hs_range_m':[0,8],'rms_range_m':[0,2.1],'minimum_coast_distance_km':50,
                  'duplicate_time':'Reject all rows at duplicate timestamps, even identical values',
                  'unknown_schema':'Reject complete track; never guess quality or substitute modeled Hs'},
            'block':{'length_km':30,'minimum_valid_samples':3,
                     'origin':'Cumulative WGS84 distance along original raw positions, before QC; reset at invalid coordinates/time or >60-second or nonpositive time gap',
                     'window':'Only QC-valid measurements inside +/-30 minutes and cutoff; one partial or full block per valid time',
                     'forecast':'Mean of serving spatial() Hs at exactly those measurement coordinates; if ANY sample has missing provider Hs, reject whole block',
                     'observed':'Arithmetic mean of the same QC-valid sensor samples; no temporal interpolation of forecast',
                     'location':'WGS84 geodesic midpoint of first and last selected measurement; coordinates used only for geographic summary'},
            'statistics':'Descriptive block-pair weighted errors; unique block, track, day and forecast-valid-time counts; no CI or independence assumption',
            'geography':'Existing disjoint gm.region coordinate sectors, not exact hydrographic basin polygons',
            'independence':'No Poseidon fitting or tuning. Upstream assimilation/calibration independence is not established.',
            'promotion':'Separate pilot, no global industrial acceptance threshold; excluded >8m seas cannot validate extreme waves'}


def acquire(folder,p):
    folder.mkdir(parents=True,exist_ok=False)
    (folder/'headers').mkdir(); (folder/'raw').mkdir()
    valid = sorted({pd.to_datetime(r['cycle'],format='%Y%m%dT%H',utc=True)+pd.Timedelta(hours=r['lead_h'])
                    for r in p['forecast_records']})
    records,failures,entries,listings = [],[],[],[]
    with httpx.Client(timeout=40,follow_redirects=True,
                      limits=httpx.Limits(max_connections=6,max_keepalive_connections=6)) as client:
        for i,url in enumerate(p['directories']):
            response = client.get(url); response.raise_for_status()
            path = folder/f'listing-{i}.html'; path.write_bytes(response.content)
            names = sorted(set(re.findall(r'href="(3ap\d{4}c\d{3}\.nc)"',response.text)))
            if not names:
                raise ValueError('RADS directory contained no expected pass files')
            listings.append({'url':url,'file':path.name,'sha256':digest(path),'count':len(names),'retrieved_at':utcnow()})
            entries.extend((url+name,name) for name in names)

        def probe(entry):
            url,name = entry
            response = client.get(url,headers={'Range':'bytes=0-16383'})
            response.raise_for_status()
            if response.status_code!=206 or len(response.content)>16384:
                raise ValueError('Server did not honor bounded 16KB metadata request')
            attributes = nc_attributes(response.content)
            start,end = header_times(attributes)
            check_identity(attributes,name,int(url.split('/')[-2][1:]))
            path = folder/'headers'/(name+'.bin'); path.write_bytes(response.content)
            return {'url':url,'filename':name,'header_file':str(path.relative_to(folder)),
                    'header_sha256':digest(path),'header_retrieved_at':utcnow(),
                    'header_last_modified':response.headers.get('last-modified'),
                    'start':start.isoformat(),'end':end.isoformat(),'attributes':attributes,
                    'selected':overlaps(start,end,valid,p['tolerance_minutes'])}

        with ThreadPoolExecutor(max_workers=6) as executor:
            tasks = {executor.submit(probe,entry):entry for entry in entries}
            for i,future in enumerate(as_completed(tasks)):
                entry = tasks[future]
                try:
                    records.append(future.result())
                except (httpx.HTTPError,ValueError,OSError,UnicodeError,KeyError) as exc:
                    failures.append({'stage':'header','url':entry[0],'error':str(exc)[:300]})
                if (i+1)%100==0:
                    print(f'Header selection {i+1}/{len(entries)}',flush=True)

        def download(record):
            response = client.get(record['url']); response.raise_for_status()
            if len(response.content)>2_000_000 or not response.content.startswith(b'CDF'):
                raise ValueError('Unexpected RADS pass size/format')
            attributes = nc_attributes(response.content[:16384]); start,end = header_times(attributes)
            # NRT files can be replaced during acquisition. Preserve and declare the
            # actual complete-file metadata; do not silently assume the header revision.
            check_identity(attributes,record['filename'],int(record['url'].split('/')[-2][1:]))
            path = folder/'raw'/record['filename']; path.write_bytes(response.content)
            return {**record,'raw_file':str(path.relative_to(folder)), 'sha256':digest(path),
                    'bytes':len(response.content),'retrieved_at':utcnow(),
                    'last_modified':response.headers.get('last-modified'),'attributes':attributes,
                    'header_start':record['start'],'header_end':record['end'],
                    'start':start.isoformat(),'end':end.isoformat(),
                    'header_revision_changed':attributes!=record['attributes']}

        selected = [r for r in records if r['selected']]
        downloaded = []
        with ThreadPoolExecutor(max_workers=6) as executor:
            tasks = {executor.submit(download,r):r for r in selected}
            for future in as_completed(tasks):
                record = tasks[future]
                try:
                    downloaded.append(future.result())
                except (httpx.HTTPError,ValueError,OSError,KeyError) as exc:
                    failures.append({'stage':'download','url':record['url'],'error':str(exc)[:300]})
    manifest = {'listings':listings,'headers':sorted(records,key=lambda r:r['filename']),
                'downloaded':sorted(downloaded,key=lambda r:r['filename']),
                'files_listed':len(entries),'files_selected':len(selected),'failures':failures}
    atomic_json(folder/'manifest.json',manifest)
    if not downloaded:
        raise ValueError('No complete matching RADS pass available; prior report preserved')
    return manifest


def verify_snapshot(folder):
    manifest = read_json(folder/'manifest.json')
    if not manifest:
        raise ValueError('Satellite snapshot manifest missing')
    for r in manifest['listings']:
        if digest(folder/r['file'])!=r['sha256']:
            raise ValueError('Satellite listing checksum mismatch')
    for r in manifest['headers']:
        if digest(folder/r['header_file'])!=r['header_sha256']:
            raise ValueError('Satellite header checksum mismatch')
    for r in manifest['downloaded']:
        if digest(folder/r['raw_file'])!=r['sha256']:
            raise ValueError('Satellite raw-file checksum mismatch')
    return manifest


def parse_track(raw):
    with xr.open_dataset(io.BytesIO(raw),engine='scipy') as dataset:
        attrs = dict(dataset.attrs); header_times(attrs)
        check_identity(attrs,attrs.get('filename',''))
        required = {'swh_ku':'m','swh_rms_ku':'m','dist_coast':'km',
                    'lat':'degrees_north','lon':'degrees_east'}
        for key,unit in required.items():
            if key not in dataset or dataset[key].attrs.get('units')!=unit:
                raise ValueError('Missing or unexpected sensor variable/units: '+key)
        if dataset['swh_ku'].attrs.get('standard_name')!='sea_surface_wave_significant_height':
            raise ValueError('Expected observed significant wave height definition')
        if 'flags' not in dataset or dataset['flags'].attrs.get('flag_meanings','').split()!=FLAGS:
            raise ValueError('Unknown Sentinel-3A QC flag schema')
        masks = np.asarray(dataset['flags'].attrs.get('flag_masks',[])).astype(np.int64)&65535
        if not np.array_equal(masks,1 << np.arange(16)):
            raise ValueError('Unknown Sentinel-3A QC bit positions')
        if 'time' not in dataset or not re.search(r'(?:UTC|Z|\+00:00)$',str(dataset['time'].encoding.get('units',''))):
            raise ValueError('Missing explicit UTC observation time')
        names = ['time','lat','lon','swh_ku','swh_rms_ku','dist_coast','flags']
        if any(dataset[k].dims!=('time',) for k in names):
            raise ValueError('Expected one-dimensional time-based trajectory')
        data = pd.DataFrame({k:dataset[k].values for k in names})
    data['time'] = pd.to_datetime(data['time'],utc=True)
    start,end = header_times(attrs)
    if len(data) and (data.time.min()<start or data.time.max()>end):
        raise ValueError('Measurement times outside declared pass coverage')
    return data,attrs


def raw_bins(data,length_km=30):
    """Positions/time only: wave values and quality do not move bin boundaries."""
    result = np.full(len(data),-1,dtype=np.int64)
    segment,distance,previous = -1,0.,None
    for i,row in enumerate(data.itertuples(index=False)):
        if pd.isna(row.time) or not np.isfinite(row.lat) or not np.isfinite(row.lon) or not -90<=row.lat<=90 or not -180<=row.lon<=360:
            previous = None
            continue
        gap = (row.time-previous.time).total_seconds() if previous is not None else None
        if previous is None or gap<=0 or gap>60:
            segment += 1; distance = 0.
        else:
            distance += GEOD.Inverse(previous.lat,previous.lon,row.lat,row.lon)['s12']/1000
        result[i] = segment*1_000_000+int(np.floor(distance/length_km))
        previous = row
    return result


def qc(data,cutoff):
    """Exclusive first-failing-rule row counts, plus clearly named overlapping hits."""
    reasons = np.full(len(data),'accepted',dtype=object)
    flags = data['flags'].to_numpy(dtype=float)
    flag_valid = np.isfinite(flags)&(flags==np.floor(flags))&(flags>=-32768)&(flags<=65535)
    unsigned = np.where(flag_valid,flags,0).astype(np.int64)&65535
    hs = data.swh_ku.to_numpy(dtype=float)
    rms = data.swh_rms_ku.to_numpy(dtype=float)
    coast = data.dist_coast.to_numpy(dtype=float)
    rules = [
        ('invalid_time',data.time.isna().to_numpy()),
        ('invalid_position',~np.isfinite(data.lat)|~np.isfinite(data.lon)|(abs(data.lat)>90)|(data.lon < -180)|(data.lon>360)),
        ('duplicate_time',data.time.duplicated(keep=False).to_numpy()),
        ('missing_or_invalid_flags',~flag_valid|(unsigned==32767)),
        ('quality_flag_rejected',(unsigned&STRICT_MASK)!=0),
        ('missing_hs',~np.isfinite(hs)),('negative_hs',hs<0),('hs_above_8',hs>8),
        ('missing_rms',~np.isfinite(rms)),('rms_outside_range',(rms<0)|(rms>2.1)),
        ('missing_coast_distance',~np.isfinite(coast)),('within_50km_of_coast',coast<50),
        ('after_cutoff',(data.time>cutoff).to_numpy())]
    hits = {}
    for name,bad in rules:
        bad = np.asarray(bad,dtype=bool)
        hits[name] = int(bad.sum())
        reasons[(reasons=='accepted')&bad] = name
    return reasons=='accepted',dict(Counter(reasons)),hits


def blocks(data,track_id,valid_times,p):
    bins = raw_bins(data,p['block']['length_km'])
    keep,counts,hits = qc(data,pd.Timestamp(p['cutoff_utc']))
    output,excluded = [],Counter()
    inside_any = np.zeros(len(data),dtype=bool)
    tolerance = pd.Timedelta(minutes=p['tolerance_minutes'])
    for when in valid_times:
        inside = ((data.time>=when-tolerance)&(data.time<=when+tolerance)).to_numpy()
        inside_any |= inside
        for number in sorted(set(bins[inside&(bins>=0)])):
            idx = np.flatnonzero(inside&keep&(bins==number))
            if len(idx)<p['block']['minimum_valid_samples']:
                excluded['blocks_below_minimum_samples'] += 1
                continue
            rows = data.iloc[idx]
            first,last = rows.iloc[0],rows.iloc[-1]
            line = GEOD.Inverse(first.lat,first.lon,last.lat,last.lon)
            midpoint = GEOD.Direct(first.lat,first.lon,line['azi1'],line['s12']/2)
            lon,lat = midpoint['lon2'],midpoint['lat2']
            times = rows.time.dt.as_unit('ns').astype('int64').to_numpy()
            nominal = pd.Timestamp(int(times[0])+int(np.rint(np.mean(times-times[0]))),tz='UTC')
            output.append({'block_id':f'{track_id}:{number}:{when.isoformat()}',
                           'track_id':track_id,'lat':float(lat),'lon':float(lon),'region':gm.region(lat,lon),
                           'n_samples':len(idx),'valid_time':when.isoformat(),'observation_time':nominal.isoformat(),
                           'observation_start':rows.time.min().isoformat(),'observation_end':rows.time.max().isoformat(),
                           'time_offset_minutes':(nominal-when).total_seconds()/60,
                           'max_abs_time_offset_minutes':float(np.max(np.abs(times-when.value)))/60e9,
                           'observed_hs_m':float(rows.swh_ku.mean()),
                           '_locations':list(zip(rows.lat.to_numpy(),rows.lon.to_numpy()))})
    excluded['rows_outside_all_time_windows'] = int((~inside_any).sum())
    return output,counts,hits,excluded


def collocate_block(block,data):
    """Reject entire block if any selected observation cannot be served."""
    values = []
    for lat,lon in block['_locations']:
        value = spatial(data,float(lat),float(lon))
        if value is None:
            return None
        values.append(value['hs'])
    if not values:
        return None
    return float(np.mean(values))


def metrics(rows):
    adapted = [{**r,'station_id':r['track_id']} for r in rows]
    value = gm.metrics(adapted,bootstrap=False)
    value['n_tracks'] = value.pop('n_stations')
    value['n_unique_observations'] = len({r['block_id'] for r in rows})
    value['n_observation_days'] = len({r['observation_time'][:10] for r in rows})
    if 'station_balanced_mean_rmse_m' in value:
        value['track_balanced_mean_rmse_m'] = value.pop('station_balanced_mean_rmse_m')
    return value


def summarize(rows):
    return {'overall':metrics(rows),
            'by_region':[{'region':key,'label':label,**metrics([r for r in rows if r['region']==key])}
                         for key,label in gm.REGIONS.items()],
            'by_lead':[{'lead_h':h,**metrics([r for r in rows if r['lead_h']==h])} for h in (6,24,48,72,120)],
            'by_hs':[{'band':key,'label':label,**metrics([r for r in rows if low<=r['observed_hs_m']<high])}
                     for key,label,low,high in gm.HS_BANDS]}


def code_fingerprints():
    files = [Path(__file__),Path(gm.__file__),Path(__file__).parents[1]/'twin'/'global_forecast.py']
    return {str(f.relative_to(Path(__file__).parents[2])):digest(f) for f in files}


def run(resume=None,offline=False):
    initial_code_hashes = code_fingerprints()
    root().mkdir(parents=True,exist_ok=True)
    with (root()/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if resume and not RUN_ID.fullmatch(resume):
            raise ValueError('Invalid satellite run ID')
        if offline and not resume:
            raise ValueError('--offline requires --resume')
        run_id = pd.Timestamp.now(tz='UTC').strftime('%Y%m%dT%H%M%SZ')
        folder = root()/'runs'/run_id; folder.mkdir(parents=True,exist_ok=False)
        if resume:
            previous = root()/'runs'/resume
            p = read_json(previous/'protocol.json')
            if not p or p['id']!='sentinel3a-open-ocean-hs-v1':
                raise ValueError('Frozen satellite protocol missing')
        else:
            buoy_run,records = frame_records()
            p = protocol(buoy_run,records)
        atomic_json(folder/'code_sha256.json',initial_code_hashes)
        atomic_json(folder/'protocol.json',p)  # Before acquisition, QC and all model-error calculations.
        atomic_json(root()/'status.json',{'state':'acquisition','run_id':run_id,'updated_at':utcnow()})
        try:
            if resume:
                shutil.copytree(previous/'observations',folder/'observations')
                manifest = verify_snapshot(folder/'observations')
            else:
                manifest = acquire(folder/'observations',p)
            valid_times = sorted({pd.to_datetime(r['cycle'],format='%Y%m%dT%H',utc=True)+pd.Timedelta(hours=r['lead_h'])
                                  for r in p['forecast_records']})
            all_blocks,failures = [],list(manifest['failures'])
            parsed_tracks = 0
            qc_counts,qc_hits,exclusions = Counter(),Counter(),Counter()
            for record in manifest['downloaded']:
                try:
                    data,attrs = parse_track((folder/'observations'/record['raw_file']).read_bytes())
                    parsed_tracks += 1
                    track_id = f"S3A:a:{attrs['cycle_number']}:{attrs['pass_number']}"
                    part,counts,hits,excluded = blocks(data,track_id,valid_times,p)
                    qc_counts.update(counts); qc_hits.update(hits); exclusions.update(excluded)
                    for b in part:
                        b.update(mission='Sentinel-3A',mission_phase='a',satellite_cycle=int(attrs['cycle_number']),
                                 pass_number=int(attrs['pass_number']),observation_sha256=record['sha256'])
                    all_blocks.extend(part)
                except (ValueError,OSError,KeyError,TypeError) as exc:
                    failures.append({'stage':'schema_or_qc','url':record['url'],'error':str(exc)[:300]})
            if not parsed_tracks:
                raise ValueError('All downloaded satellite tracks failed schema validation; prior report preserved')
            print(f"Satellite acquisition: {len(manifest['downloaded'])} files, {len(all_blocks)} eligible blocks",flush=True)
            rows = []
            archive = settings.data_root/'validation'/'global'/'archive'
            for i,record in enumerate(p['forecast_records']):
                cycle,lead = record['cycle'],record['lead_h']
                path = archive/cycle/f'f{lead:03d}.npz'
                if digest(path)!=record['sha256_npz'] or digest(path.with_suffix('.grib2'))!=record['sha256_grib']:
                    raise ValueError('Read-only forecast archive checksum mismatch')
                when = (pd.to_datetime(cycle,format='%Y%m%dT%H',utc=True)+pd.Timedelta(hours=lead)).isoformat()
                with np.load(path,allow_pickle=False) as file:
                    fields = {name:file[name] for name in FIELDS}
                for b in all_blocks:
                    if b['valid_time']!=when:
                        continue
                    value = collocate_block(b,fields)
                    if value is None:
                        exclusions['provider_missing_block_pairs'] += 1
                        continue
                    row = {k:v for k,v in b.items() if k!='_locations'}
                    row.update(cycle=cycle,lead_h=lead,forecast_hs_m=value,error_m=value-b['observed_hs_m'],
                               forecast_sha256_grib=record['sha256_grib'])
                    rows.append(row)
                print(f'Satellite comparison {i+1}/{len(p["forecast_records"])}: {len(rows)} cumulative block pairs',flush=True)
            with (folder/'pairs.csv').open('w',newline='') as file:
                writer = csv.DictWriter(file,fieldnames=PAIR_COLUMNS); writer.writeheader(); writer.writerows(rows)
            summary = summarize(rows)
            unique = {r['block_id']:r for r in rows}
            block_points = [{k:r[k] for k in ('block_id','track_id','lat','lon','region','observation_time','observed_hs_m','n_samples')}
                            for r in unique.values()]
            report = {'schema_version':'global-altimeter-validation-1.0','state':'partial' if rows else 'unavailable',
                      'run_id':run_id,'replayed_from':resume,'generated_at':utcnow(),'model':SOURCE,
                      'variable':'hs','observation_provider':'NOAA RADS / Sentinel-3A radar altimeter',
                      'protocol':p,**summary,'block_points':block_points,
                      'coverage':{'global_ready':False,'missing_regions':[r['label'] for r in summary['by_region'] if not r['n']],
                                  'valid_start':min((r['valid_time'] for r in rows),default=None),
                                  'valid_end':max((r['valid_time'] for r in rows),default=None),
                                  'measurement_start':min((r['observation_start'] for r in rows),default=None),
                                  'measurement_end':max((r['observation_end'] for r in rows),default=None)},
                      'acquisition':{'files_listed':manifest['files_listed'],'headers_downloaded':len(manifest['headers']),
                                     'files_selected':manifest['files_selected'],'files_downloaded':len(manifest['downloaded']),
                                     'download_bytes':sum(r['bytes'] for r in manifest['downloaded']),
                                     'forecast_frames_available':len(p['forecast_records']),'eligible_unique_blocks':len(all_blocks),
                                     'row_qc_exclusive':dict(qc_counts),'row_qc_overlapping_rule_hits':dict(qc_hits),
                                     'exclusions':dict(exclusions),'failures':failures,
                                     'count_units':'QC counts are raw rows; block exclusions unique track/time-window bins; provider exclusions forecast-cycle/block pairs'},
                      'source_records':manifest['downloaded'],
                      'artifact_sha256':{name:digest(folder/name) for name in ('protocol.json','pairs.csv','observations/manifest.json')},
                      'code_sha256':initial_code_hashes,
                      'downloads':{'report':f'/v1/global/validation/altimeter/report.json?run_id={run_id}',
                                   'pairs':f'/v1/global/validation/altimeter/pairs.csv?run_id={run_id}'},
                      'caveats':['부이 검증과 분리한 Sentinel-3A 외해 관측 대조입니다. 전 지구 정확도 승인·인증이 아닙니다.',
                                 '품질 조건에서 관측 Hs 8 m 초과를 제외하므로 극한 파랑의 정확도를 검증하지 못합니다.',
                                 '50 km 미만 연안, 품질 불량·해빙 조건 및 표본 없는 해역은 결과를 일반화할 수 없습니다.',
                                 '약 30 km 구간 평균과 같은 관측의 여러 예보 사이클 대조는 서로 상관됩니다. 표본 수는 독립 실험 수가 아닙니다.',
                                 '관측 시각과 예보 유효시각의 허용 차이는 ±30분입니다. 예보의 시간 보간은 수행하지 않았습니다.',
                                 'Poseidon은 이 관측에 맞추어 보정하지 않았습니다. 상위 NOAA 모델의 동화·개발에서 완전히 독립했는지는 확인하지 못했습니다.',
                                 '센서 NRT/ST 자료는 이후 재처리로 바뀔 수 있어, 이 결과는 원본과 해시를 보존한 당시 수집본에 한정됩니다.',
                                 '구역은 좌표로 나눈 보고용 구역이며 정밀 해양 분지 경계가 아닙니다.']}
            if code_fingerprints()!=initial_code_hashes:
                raise RuntimeError('Validation code changed during run; prior report preserved, replay after code freezes')
            atomic_json(folder/'report.json',report)
            atomic_json(root()/'latest.json',{'run_id':run_id,'generated_at':report['generated_at']})
            atomic_json(root()/'status.json',{'state':'complete','run_id':run_id,'updated_at':utcnow()})
            print(json.dumps({'run_id':run_id,'overall':summary['overall'],'missing_regions':report['coverage']['missing_regions']},ensure_ascii=False),flush=True)
            return report
        except Exception as exc:
            atomic_json(folder/'failure.json',{'error':str(exc),'at':utcnow()})
            atomic_json(root()/'status.json',{'state':'failed','run_id':run_id,'error':str(exc),'updated_at':utcnow()})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume',help='Replay an immutable frozen satellite run into a new run')
    parser.add_argument('--offline',action='store_true',help='Require the saved observation snapshot and existing forecast archive')
    args = parser.parse_args()
    run(args.resume,args.offline)


if __name__=='__main__':
    main()
