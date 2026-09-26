"""Validation exports keep run identity and missing evidence distinct from zero."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from poseidon.api.app import app
from poseidon.api import global_validation as api
from poseidon.validation import global_audit as audit
from poseidon.ingest.global_wave import atomic_json

pytestmark = pytest.mark.integration


def test_global_validation_unavailable_and_rejects_path_input(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'root',lambda:tmp_path)
    monkeypatch.setattr(audit,'root',lambda:tmp_path)
    client = TestClient(app)
    response = client.get('/v1/global/validation')
    assert response.status_code==200
    assert response.json()['state']=='unavailable'
    assert 'overall' not in response.json()
    assert client.get('/v1/global/validation/pairs.csv').status_code==404
    assert client.get('/v1/global/validation',params={'run_id':'../../bad'}).status_code==422
    assert client.get('/v1/global/validation',params={'run_id':'20260901T000000Z'}).status_code==404


def test_downloads_use_pinned_run_even_when_latest_changes(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'root',lambda:tmp_path)
    monkeypatch.setattr(audit,'root',lambda:tmp_path)
    old, new = '20260901T000000Z','20260902T000000Z'
    for rid,value in [(old,'1.0'),(new,'2.0')]:
        folder=tmp_path/'runs'/rid
        folder.mkdir(parents=True)
        (folder/'pairs.csv').write_text('forecast_hs_m\n'+value+'\n')
        atomic_json(folder/'report.json',{'run_id':rid,'state':'partial','overall':{'n':1},
            'artifact_sha256':{'pairs.csv':hashlib.sha256((folder/'pairs.csv').read_bytes()).hexdigest()}})
    atomic_json(tmp_path/'latest.json',{'run_id':new})
    client=TestClient(app)
    assert client.get('/v1/global/validation').json()['run_id']==new
    report=client.get('/v1/global/validation/report.json',params={'run_id':old})
    csv=client.get('/v1/global/validation/pairs.csv',params={'run_id':old})
    assert report.status_code==csv.status_code==200
    assert report.json()['run_id']==old
    assert csv.text=='forecast_hs_m\n1.0\n'
    assert hashlib.sha256(csv.content).hexdigest()==report.json()['artifact_sha256']['pairs.csv']
    assert old in csv.headers['content-disposition']


def test_satellite_report_remains_separate_from_buoys(tmp_path,monkeypatch):
    satellite = tmp_path/'satellite'
    monkeypatch.setattr(api,'root',lambda:tmp_path/'buoy')
    monkeypatch.setattr(audit,'root',lambda:tmp_path/'buoy')
    monkeypatch.setattr(api,'altimeter_root',lambda:satellite)
    client = TestClient(app)
    assert client.get('/v1/global/validation/altimeter').json()['state']=='unavailable'
    rid='20260902T000000Z'
    folder=satellite/'runs'/rid
    folder.mkdir(parents=True)
    atomic_json(folder/'report.json',{'run_id':rid,'state':'partial','overall':{'n':3,'n_tracks':2}})
    (folder/'pairs.csv').write_text('observed_hs_m\n3.2\n')
    atomic_json(satellite/'latest.json',{'run_id':rid})
    assert client.get('/v1/global/validation').json()['state']=='unavailable'
    assert client.get('/v1/global/validation/altimeter').json()['overall']['n_tracks']==2
    assert client.get('/v1/global/validation/altimeter/report.json',params={'run_id':rid}).json()['run_id']==rid
    assert client.get('/v1/global/validation/altimeter/pairs.csv',params={'run_id':rid}).status_code==200
    assert client.get('/v1/global/validation/altimeter',params={'run_id':'../bad'}).status_code==422
