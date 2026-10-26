"""Helm map geometry must report source and reject absent source data."""

import pytest
from fastapi.testclient import TestClient
from shapely import STRtree
from shapely.geometry import box, shape

from poseidon.api import helm_coast as coast_module
from poseidon.api.app import app
from poseidon.core.config import settings

pytestmark = pytest.mark.integration


def test_coast_window_clips_synthetic_land(monkeypatch):
    # Contract fixture only: this rectangle is not evidence about real geography.
    tree = STRtree([box(126.9, 37.5, 127.1, 37.7), box(0, 0, 1, 1)])
    monkeypatch.setattr(coast_module, "land_index", lambda: tree)
    with TestClient(app) as client:
        response = client.get("/v1/helm/coast", params={"lat": 37.56, "lon": 126.98})
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ready"
    assert result["center"] == [126.98, 37.56]
    assert result["source"] == "Natural Earth 1:10m land v5.1.1"
    assert result["radius_degrees"] == 0.03
    assert len(result["geometries"]) == 1
    assert shape(result["geometries"][0]).bounds == pytest.approx((126.95, 37.53, 127.01, 37.59))


def test_coast_window_contains_real_seoul_land_and_busan_edge():
    if not (settings.data_root / "static/natural-earth-10m/ne_10m_land.shp").exists():
        pytest.skip("local Natural Earth snapshot not present; real coast check not run")
    with TestClient(app) as client:
        seoul = client.get("/v1/helm/coast", params={"lat": 37.56, "lon": 126.98})
        busan = client.get("/v1/helm/coast", params={"lat": 35.065, "lon": 129.135})
    assert seoul.status_code == busan.status_code == 200
    assert seoul.json()["source"] == "Natural Earth 1:10m land v5.1.1"
    assert seoul.json()["geometries"]
    assert busan.json()["geometries"]
    assert seoul.json()["radius_degrees"] == 0.03


def test_bad_latitude_rejected():
    with TestClient(app) as client:
        response = client.get("/v1/helm/coast", params={"lat": 90, "lon": 0})
    assert response.status_code == 422


def test_missing_land_source_is_unavailable(monkeypatch):
    monkeypatch.setattr(coast_module, "land_index", lambda: None)
    with TestClient(app) as client:
        response = client.get("/v1/helm/coast", params={"lat": 35.065, "lon": 129.135})
    assert response.status_code == 503
