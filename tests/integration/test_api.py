"""API 계약 테스트 (integration: 로컬 데이터 레이크 필요 — nightly 전용).

docs/api/openapi.yaml의 핵심 계약: 점 예보는 physics_raw·corrected를 항상 포함한다.
"""

import numpy as np
import pytest

from poseidon.core.config import settings

pytestmark = pytest.mark.integration

_HAS_DATA = settings.catalog_path.exists()
skip_no_data = pytest.mark.skipif(not _HAS_DATA, reason="local data lake not present")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from poseidon.api.app import app
    return TestClient(app)


@skip_no_data
def test_system_cycles(client):
    r = client.get("/v1/system/cycles")
    assert r.status_code == 200
    rows = r.json()
    assert rows and {"cycle", "engine", "status"} <= set(rows[0])


@skip_no_data
def test_field_meta_and_png(client):
    meta = client.get("/v1/field/meta").json()
    assert meta["leads_h"][0] == 0.0
    assert meta["bounds"]["west"] < meta["bounds"]["east"]
    png = client.get(f"/v1/field/hs.png?cycle={meta['cycle']}&lead=0")
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"
    assert png.content[:4] == b"\x89PNG"


@skip_no_data
def test_point_forecast_transparency_contract(client):
    r = client.get("/v1/forecast/point?lat=30&lon=135")
    assert r.status_code == 200
    p = r.json()
    assert isinstance(p["corrected"], bool)
    for it in p["items"]:
        assert "physics_raw" in it and "q50" in it      # 물리 원값 항상 보존
        assert it["q50"] >= 0
    # 육지 요청은 4xx
    assert client.get("/v1/forecast/point?lat=37.5&lon=127.0").status_code == 400


@skip_no_data
def test_tide_busan(client):
    r = client.get("/v1/tide/busan?hours=24")
    if r.status_code == 404:
        pytest.skip("busan constants not fitted")
    vals = np.array(r.json()["values"])
    assert len(vals) == 144
    assert 0.2 < vals.max() - vals.min() < 3.0          # 부산 조차의 상식 범위


@skip_no_data
def test_multivariable_forecast(client):
    from datetime import datetime
    r = client.get('/v1/forecast/point?lat=30&lon=135&vars=hs,tp,tm01,tm02,dirm,dirp&level=L1')
    assert r.status_code == 200
    p = r.json()
    assert p['level'] == 'L1'
    for item in p['items']:
        assert set(item['values']) == {'hs', 'tp', 'tm01', 'tm02', 'dirm', 'dirp'}
        assert datetime.fromisoformat(item['valid_time']).utcoffset().total_seconds() == 0
        assert 'verdict' in item['applicability']
    assert any(it['values']['tp'] is not None for it in p['items'])
    assert client.get('/v1/forecast/point?lat=30&lon=135&vars=wrong').status_code == 422
    assert client.get('/v1/forecast/point?lat=30&lon=135&level=wrong').status_code == 422
    meta = client.get('/v1/field/meta').json()
    assert all(datetime.fromisoformat(v).tzinfo for v in meta['valid_times'])
