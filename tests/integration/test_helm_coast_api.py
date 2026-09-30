"""Helm map geometry must report source and reject absent source data."""

from fastapi.testclient import TestClient

from poseidon.api.app import app
from poseidon.api import helm_coast as coast_module


def test_coast_window_contains_real_seoul_land_and_busan_edge():
    with TestClient(app) as client:
        seoul = client.get('/v1/helm/coast', params={'lat': 37.56, 'lon': 126.98})
        busan = client.get('/v1/helm/coast', params={'lat': 35.065, 'lon': 129.135})
    assert seoul.status_code == busan.status_code == 200
    assert seoul.json()['source'] == 'Natural Earth 1:10m land v5.1.1'
    assert seoul.json()['geometries']
    assert busan.json()['geometries']
    assert seoul.json()['radius_degrees'] == 0.03


def test_bad_latitude_rejected():
    with TestClient(app) as client:
        response = client.get('/v1/helm/coast', params={'lat': 90, 'lon': 0})
    assert response.status_code == 422


def test_missing_land_source_is_unavailable(monkeypatch):
    monkeypatch.setattr(coast_module, 'land_index', lambda: None)
    with TestClient(app) as client:
        response = client.get('/v1/helm/coast', params={'lat': 35.065, 'lon': 129.135})
    assert response.status_code == 503
