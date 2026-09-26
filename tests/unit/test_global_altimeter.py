import io

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from poseidon.validation import global_altimeter as ga


def rows(n=6):
    return pd.DataFrame({'time':pd.date_range('2026-09-04T00:00:00Z',periods=n,freq='s'),
                         'lat':np.zeros(n),'lon':np.arange(n)*.05,
                         'swh_ku':np.ones(n)*2,'swh_rms_ku':np.ones(n)*.3,
                         'dist_coast':np.ones(n)*100,'flags':np.ones(n)})


def policy():
    return ga.protocol('20260911T170000Z',[])


def track_bytes(sensor='swh_ku',flag_meanings=None):
    data = rows()
    attrs = {'source':'radar altimeter','featureType':'trajectory','mission_name':'SNTNL-3A',
             'filename':'3ap0550c143.nc','cycle_number':143,'pass_number':550,
             'mission_phase':'a','first_meas_time':'2026-09-04 00:00:00.000000',
             'last_meas_time':'2026-09-04 00:00:05.000000'}
    dataset = xr.Dataset({sensor:('time',data.swh_ku.values,{'units':'m','standard_name':'sea_surface_wave_significant_height'}),
                          'swh_rms_ku':('time',data.swh_rms_ku.values,{'units':'m'}),
                          'dist_coast':('time',data.dist_coast.values,{'units':'km'}),
                          'flags':('time',data['flags'].values.astype('int16'),
                                   {'flag_meanings':' '.join(flag_meanings or ga.FLAGS),
                                    'flag_masks':np.array([1<<i for i in range(16)],dtype=np.uint16).view(np.int16)})},
                         coords={'time':('time',data.time.values),
                                 'lat':('time',data.lat.values,{'units':'degrees_north'}),
                                 'lon':('time',data.lon.values,{'units':'degrees_east'})},attrs=attrs)
    dataset.time.encoding['units']='seconds since 1985-01-01 00:00:00 UTC'
    return bytes(dataset.to_netcdf(engine='scipy'))


def test_header_only_reads_classic_attributes_and_rejects_truncation():
    raw = track_bytes()
    attrs = ga.nc_attributes(raw)
    assert attrs['source']=='radar altimeter'
    assert ga.header_times(attrs)[0]==pd.Timestamp('2026-09-04T00:00:00Z')
    with pytest.raises(ValueError):
        ga.nc_attributes(raw[:60])
    with pytest.raises(ValueError):
        ga.nc_attributes(b'not netcdf')


def test_sensor_schema_rejects_model_hs_substitution():
    data,attrs = ga.parse_track(track_bytes())
    assert len(data)==6
    assert data.swh_ku.iloc[0]==2
    with pytest.raises(ValueError,match='sensor variable'):
        ga.parse_track(track_bytes('swh_mfwam'))
    with pytest.raises(ValueError,match='QC flag schema'):
        ga.parse_track(track_bytes(flag_meanings=['unknown']*16))


def test_sar_bit_is_valid_unused_bit_valid_bad_and_signed_bits_rejected():
    data = rows(8)
    data['flags'] = [1,0,16385,4097,-32767,32767,np.nan,1.25]
    keep,counts,hits = ga.qc(data,pd.Timestamp('2026-09-11T16:00Z'))
    assert keep.tolist()==[True,True,True,False,False,False,False,False]
    assert counts['accepted']==3
    assert counts['quality_flag_rejected']==2
    assert counts['missing_or_invalid_flags']==3
    assert sum(counts.values())==len(data)
    assert hits['quality_flag_rejected']>=counts['quality_flag_rejected']


def test_qc_boundaries_duplicate_rejection_and_exclusive_counts():
    data = rows(8)
    data.loc[0,'dist_coast']=50
    data.loc[1,'dist_coast']=49.999
    data.loc[2,'swh_ku']=8
    data.loc[3,'swh_ku']=8.001
    data.loc[4,'swh_rms_ku']=2.101
    data.loc[5,'swh_rms_ku']=2.1
    data.loc[7,'time']=data.loc[6,'time']
    keep,counts,hits = ga.qc(data,pd.Timestamp('2026-09-11T16:00Z'))
    assert keep.tolist()==[True,False,True,False,False,True,False,False]
    assert counts['duplicate_time']==2
    assert counts['hs_above_8']==1
    assert sum(counts.values())==8


def test_bins_use_raw_positions_before_wave_quality_and_restart_at_gap():
    data = rows(8)
    data['lon']=np.arange(8)*.1  # 11.1319 km increments on equator.
    expected = [0,0,0,1,1,1,2,2]
    assert ga.raw_bins(data).tolist()==expected
    data.loc[2,'flags']=4097; data.loc[3,'swh_ku']=30
    assert ga.raw_bins(data).tolist()==expected
    data.loc[6:,'time'] += pd.Timedelta(seconds=100)
    assert ga.raw_bins(data).tolist()==[0,0,0,1,1,1,1000000,1000000]


def test_bins_do_not_jump_across_dateline():
    data = rows(4)
    data['lon']=[179.90,179.95,-180,-179.95]
    assert ga.raw_bins(data).tolist()==[0,0,0,0]


def test_blocks_hand_calculated_means_time_window_and_minimum_count():
    data = rows(8)
    data['lon']=np.arange(8)*.1
    data['swh_ku']=[1,2,3,4,5,6,7,8]
    when = pd.Timestamp('2026-09-04T00:00Z')
    blocks,counts,hits,excluded = ga.blocks(data,'track',[when],policy())
    assert [b['observed_hs_m'] for b in blocks]==[2,5]
    assert [b['n_samples'] for b in blocks]==[3,3]
    assert excluded['blocks_below_minimum_samples']==1
    assert blocks[0]['observation_time']=='2026-09-04T00:00:01+00:00'
    assert blocks[0]['max_abs_time_offset_minutes']==pytest.approx(2/60)
    assert blocks[0]['lon']==pytest.approx(.1)
    out,_,_,excluded = ga.blocks(data,'track',[when+pd.Timedelta(minutes=31)],policy())
    assert out==[] and excluded['rows_outside_all_time_windows']==8


def test_time_window_boundaries_and_cutoff_are_inclusive():
    when = pd.Timestamp('2026-09-04T00:00Z')
    assert ga.overlaps(when-pd.Timedelta(hours=1),when-pd.Timedelta(minutes=30),[when])
    assert not ga.overlaps(when-pd.Timedelta(hours=1),when-pd.Timedelta(minutes=30,seconds=1),[when])
    data = rows(3)
    keep,counts,_ = ga.qc(data,data.time.iloc[1])
    assert keep.tolist()==[True,True,False]
    assert counts['after_cutoff']==1


def test_same_samples_collocated_before_mean_and_missing_rejects_whole_block(monkeypatch):
    block = {'_locations':[(0,1),(0,3),(0,5)]}
    monkeypatch.setattr(ga,'spatial',lambda data,lat,lon:{'hs':lon*lon})
    assert ga.collocate_block(block,{})==pytest.approx(35/3)
    monkeypatch.setattr(ga,'spatial',lambda data,lat,lon:None if lon==3 else {'hs':lon})
    assert ga.collocate_block(block,{}) is None
    def bad(*args):
        raise RuntimeError('Unexpected numerical failure')
    monkeypatch.setattr(ga,'spatial',bad)
    with pytest.raises(RuntimeError,match='numerical failure'):
        ga.collocate_block(block,{})


def test_unique_blocks_counted_once_across_forecast_cycles_and_empty_regions_kept():
    row = {'block_id':'b1','track_id':'t1','observation_time':'2026-09-04T00:00:01Z',
           'valid_time':'2026-09-04T00:00:00Z','observed_hs_m':2.,'forecast_hs_m':3.,
           'cycle':'20260903T00','lead_h':24,'region':'indian'}
    second = {**row,'cycle':'20260902T00','lead_h':48,'forecast_hs_m':1.}
    result = ga.summarize([row,second])
    assert result['overall']['rmse_m']==1
    assert result['overall']['bias_m']==0
    assert result['overall']['n']==2
    assert result['overall']['n_unique_observations']==1
    assert result['overall']['n_tracks']==1
    assert result['overall']['n_observation_days']==1
    assert result['overall']['cycle_bootstrap_95'] is None
    assert len(result['by_region'])==8
    assert sum(r['n']==0 for r in result['by_region'])==7


def test_latest_pointer_path_is_validated(tmp_path,monkeypatch):
    monkeypatch.setattr(ga,'root',lambda:tmp_path)
    ga.atomic_json(tmp_path/'latest.json',{'run_id':'../../sensitive'})
    assert ga.latest_report() is None
    ga.atomic_json(tmp_path/'latest.json',{'run_id':None})
    assert ga.latest_report() is None


def test_pass_identity_matches_filename_cycle_and_directory():
    attrs = ga.nc_attributes(track_bytes())
    ga.check_identity(attrs,'3ap0550c143.nc',143)
    with pytest.raises(ValueError,match='identity'):
        ga.check_identity(attrs,'3ap0550c143.nc',144)
    with pytest.raises(ValueError,match='identity'):
        ga.check_identity({**attrs,'pass_number':549},'3ap0550c143.nc',143)


def test_microsecond_and_nanosecond_times_produce_identical_blocks():
    data = rows()
    when = pd.Timestamp('2026-09-04T00:00Z')
    us = data.copy(); us['time']=us.time.dt.as_unit('us')
    ns = data.copy(); ns['time']=ns.time.dt.as_unit('ns')
    a = ga.blocks(us,'track',[when],policy())[0]
    b = ga.blocks(ns,'track',[when],policy())[0]
    assert a==b
    assert a[0]['observation_time'].startswith('2026-09-04T00:00:02')


def fake_acquisition(raw):
    def acquire(folder,p):
        folder.mkdir()
        path = folder/'sample.nc'; path.write_bytes(raw)
        manifest = {'failures':[],'files_listed':1,'headers':[],'files_selected':1,
                    'downloaded':[{'raw_file':'sample.nc','sha256':ga.digest(path),'url':'https://example.invalid/sample',
                                   'bytes':len(raw)}]}
        ga.atomic_json(folder/'manifest.json',manifest)
        return manifest
    return acquire


def test_all_schema_failures_preserve_published_report(tmp_path,monkeypatch):
    monkeypatch.setattr(ga,'root',lambda:tmp_path)
    monkeypatch.setattr(ga,'frame_records',lambda:('previous',[]))
    monkeypatch.setattr(ga,'acquire',fake_acquisition(b'not a NetCDF observation'))
    ga.atomic_json(tmp_path/'latest.json',{'run_id':'20260101T000000Z'})
    with pytest.raises(ValueError,match='All downloaded satellite tracks failed'):
        ga.run()
    assert ga.read_json(tmp_path/'latest.json')['run_id']=='20260101T000000Z'
    assert ga.read_json(tmp_path/'status.json')['state']=='failed'


def test_code_changes_during_run_do_not_publish(tmp_path,monkeypatch):
    monkeypatch.setattr(ga,'root',lambda:tmp_path)
    monkeypatch.setattr(ga,'frame_records',lambda:('previous',[]))
    monkeypatch.setattr(ga,'acquire',fake_acquisition(track_bytes()))
    calls = iter([{'module':'before'},{'module':'after'}])
    monkeypatch.setattr(ga,'code_fingerprints',lambda:next(calls))
    ga.atomic_json(tmp_path/'latest.json',{'run_id':'20260101T000000Z'})
    with pytest.raises(RuntimeError,match='code changed during run'):
        ga.run()
    assert ga.read_json(tmp_path/'latest.json')['run_id']=='20260101T000000Z'
