"""Catalog links must not survive tampered coordinates or fabricate provenance."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from poseidon.api.port_voyage import resolve_port_references


def setup_catalog(monkeypatch, *, port=None, status="available"):
    from poseidon.ports import catalog
    canonical = {"id": "unlocode:KRPUS", "name": "Busan", "country_code": "KR",
                 "lat": 35.1, "lon": 129.04, "coordinate_source": "wpi",
                 "sources": [{"id": "wpi", "record_id": "123"}]}
    monkeypatch.setattr(catalog, "catalog_snapshot", lambda keys: ({
        "status": status, "catalog_id": "fixed-version", "catalog_sha256": "a" * 64,
        "sources": [{"id": "wpi", "sha256": "b" * 64, "files": [{"sha256": "c" * 64}]}]},
        {key: canonical if port is None else port for key in keys}))
    return canonical


def waypoint(**values):
    return SimpleNamespace(port_id="unlocode:KRPUS", lat=values.get("lat", 35.1),
                           lon=values.get("lon", 129.04), name="untrusted name")


def test_unlinked_waypoint_has_no_dependency_on_catalog():
    assert resolve_port_references([SimpleNamespace(port_id=None)]) == []


def test_verified_port_snapshot_uses_server_name_source_and_catalog(monkeypatch):
    canonical = setup_catalog(monkeypatch)
    p = waypoint()
    refs = resolve_port_references([p])
    assert p.name == "Busan"
    assert refs[0]["sources"] == canonical["sources"]
    assert refs[0]["catalog_id"] == "fixed-version"
    assert refs[0]["reference_only"] is True
    assert refs[0]["waypoint_index"] == 0
    assert refs[0]["source_snapshots"][0]["files"][0]["sha256"] == "c" * 64


@pytest.mark.parametrize("values", [{"lat": 35.101}, {"lon": 129.0401}])
def test_altered_port_coordinates_rejected(monkeypatch, values):
    setup_catalog(monkeypatch)
    with pytest.raises(HTTPException) as error:
        resolve_port_references([waypoint(**values)])
    assert error.value.status_code == 422


def test_missing_coordinates_and_catalog_fail_closed(monkeypatch):
    setup_catalog(monkeypatch, port={"lat": None, "lon": None})
    with pytest.raises(HTTPException) as error:
        resolve_port_references([waypoint()])
    assert error.value.status_code == 422
    setup_catalog(monkeypatch, status="unavailable")
    with pytest.raises(HTTPException) as error:
        resolve_port_references([waypoint()])
    assert error.value.status_code == 503


def test_unknown_port_rejected(monkeypatch):
    from poseidon.ports import catalog
    setup_catalog(monkeypatch)
    monkeypatch.setattr(catalog, "catalog_snapshot", lambda keys: ({"status": "ready"}, {}))
    with pytest.raises(HTTPException) as error:
        resolve_port_references([waypoint()])
    assert error.value.status_code == 422
