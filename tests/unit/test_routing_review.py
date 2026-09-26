"""Independent adversarial checks for maritime route geometry and graph search.

These cases distinguish continuous coast intersection from point-only checks,
and require routing failure to remain failure rather than a direct-line route.
"""
import pytest
from shapely import STRtree
from shapely.geometry import Point, box

from poseidon.routing.engine import (
    RoutingError,
    geodesic_pieces,
    screen_edge,
    shortest_path,
)


pytestmark = pytest.mark.unit


def graph(node_count, edges):
    adjacency = [[] for _ in range(node_count)]
    for edge_id, u, v, weight, passage in edges:
        adjacency[u].append((v, weight, edge_id, passage))
        adjacency[v].append((u, weight, edge_id, passage))
    return adjacency


def test_thin_island_between_densification_points_is_rejected():
    coordinates = [[-0.05, 0], [0.05, 0]]
    island = box(0.009, -0.001, 0.010, 0.001)
    distance, pieces = geodesic_pieces(coordinates)
    # This narrow obstacle does not coincide with a sampled vertex.
    assert not any(island.intersects(Point(p)) for line in pieces for p in line.coords)
    checked_distance, clear = screen_edge(coordinates, STRtree([island]))
    assert checked_distance == pytest.approx(distance)
    assert clear is False


def test_native_network_bend_is_preserved_instead_of_shortcutting_land():
    coordinates = [[0, 0], [0, 0.2], [0.2, 0.2]]
    island = box(0.07, 0.07, 0.13, 0.13)
    distance, pieces = geodesic_pieces(coordinates)
    assert 23.9 < distance < 24.1
    assert any(p[0] == pytest.approx(0, abs=1e-12)
               and p[1] == pytest.approx(0.2, abs=1e-12)
               for line in pieces for p in line.coords)
    assert not any(line.intersects(island) for line in pieces)
    assert screen_edge(coordinates, STRtree([island]))[1] is True


@pytest.mark.parametrize("coordinates", [
    [[179, 0], [-179, 0]],
    [[-179, 0], [179, 0]],
    [[179, 0], [180, 0], [-180, 0], [-179, 0]],
])
def test_dateline_is_short_and_never_creates_greenwich_crossing(coordinates):
    distance, pieces = geodesic_pieces(coordinates)
    assert distance == pytest.approx(120.2154328221, abs=1e-7)
    assert pieces
    for line in pieces:
        assert all(abs(lon) >= 179 - 1e-9 for lon, lat in line.coords)
        assert all(abs(a[0] - b[0]) <= 180 for a, b in zip(line.coords, list(line.coords)[1:]))
    assert screen_edge(coordinates, STRtree([box(-1, -1, 1, 1)]))[1] is True


@pytest.mark.parametrize("island", [
    box(179.99, -0.01, 180, 0.01),
    box(-180, -0.01, -179.99, 0.01),
])
def test_land_on_either_side_of_dateline_is_still_rejected(island):
    assert screen_edge([[179, 0], [-179, 0]], STRtree([island]))[1] is False


def test_high_latitude_geodesic_arc_is_checked_instead_of_lon_lat_chord():
    coordinates = [[-45, 75], [45, 75]]
    distance, pieces = geodesic_pieces(coordinates)
    assert distance == pytest.approx(1271.6836974608, abs=1e-7)
    assert max(lat for line in pieces for lon, lat in line.coords) > 79.27
    # WGS84 arc crosses this obstacle; a flat line at 75 N would miss it.
    assert screen_edge(coordinates, STRtree([box(-1, 79.1, 1, 79.4)]))[1] is False


@pytest.mark.parametrize("coordinates", [
    [[0.5, 0], [2, 0]],
    [[2, 0], [0.5, 0]],
    [[1, 0], [2, 0]],
])
def test_land_or_boundary_endpoint_does_not_bypass_intersection(coordinates):
    assert screen_edge(coordinates, STRtree([box(0, -1, 1, 1)]))[1] is False


def test_shortest_path_uses_distance_and_applies_passage_closure():
    adjacency = graph(4, [
        (10, 0, 1, 2, "panama"),
        (11, 1, 3, 2, None),
        (12, 0, 2, 3, None),
        (13, 2, 3, 4, None),
        (14, 0, 3, 9, "suez"),
    ])
    assert shortest_path(adjacency, 0, 3, set()) == (4, [0, 1, 3], [10, 11])
    assert shortest_path(adjacency, 0, 3, {"panama"}) == (7, [0, 2, 3], [12, 13])
    assert shortest_path(adjacency, 0, 3, {"panama", "suez"}) == (7, [0, 2, 3], [12, 13])


def test_reverse_route_preserves_edge_order_and_distance():
    adjacency = graph(3, [(7, 0, 1, 1.25, None), (9, 1, 2, 2.75, None)])
    assert shortest_path(adjacency, 0, 2, set()) == (4, [0, 1, 2], [7, 9])
    assert shortest_path(adjacency, 2, 0, set()) == (4, [2, 1, 0], [9, 7])


def test_disconnected_graph_has_no_direct_line_fallback():
    adjacency = graph(4, [(5, 0, 1, 1, None), (6, 2, 3, 1, None)])
    with pytest.raises(RoutingError) as error:
        shortest_path(adjacency, 0, 3, set())
    assert error.value.code == "no_route"


def test_all_available_passages_closed_returns_no_route():
    adjacency = graph(3, [
        (1, 0, 1, 1, "panama"),
        (2, 1, 2, 1, None),
        (3, 0, 2, 3, "suez"),
    ])
    with pytest.raises(RoutingError) as error:
        shortest_path(adjacency, 0, 2, {"panama", "suez"})
    assert error.value.code == "no_route"


def test_same_endpoint_is_not_a_successful_zero_distance_voyage():
    with pytest.raises(RoutingError) as error:
        shortest_path([[]], 0, 0, set())
    assert error.value.code == "same_endpoint"


def test_missing_coastline_cannot_produce_a_clear_route():
    with pytest.raises(RoutingError) as error:
        screen_edge([[0, 0], [1, 0]], None)
    assert error.value.code == "coast_unavailable"


@pytest.mark.parametrize("weight", [float("nan"), float("inf"), -1, 0])
def test_invalid_graph_weight_does_not_create_a_route(weight):
    with pytest.raises(RoutingError) as error:
        shortest_path(graph(2, [(1, 0, 1, weight, None)]), 0, 1, set())
    assert error.value.code == "invalid_network"


def test_saved_route_preserves_inputs_provenance_and_original_geometry(tmp_path, monkeypatch):
    from poseidon.routing import store

    monkeypatch.setattr(store, "plans_root", lambda: tmp_path)
    candidate = {
        "scope": "network_segment_only",
        "waypoints": [{"lat": 0, "lon": 179}, {"lat": 0, "lon": -179}],
        "departure": {"port": {"id": "port:a", "lat": 0, "lon": 178.9}, "gap_nm": 6},
        "network": {"graph_sha256": "a" * 64},
        "coast": {"files_sha256": {"land.shp": "b" * 64}},
        "constraints": {"blocked_passages": ["panama", "suez"]},
    }
    saved = store.save_plan(candidate)
    assert store.load_plan(saved["plan_id"]) == saved
    assert saved["waypoints"] == candidate["waypoints"]
    assert store.save_plan(candidate)["plan_id"] == saved["plan_id"]
    changed = {**candidate, "constraints": {"blocked_passages": ["panama", "suez", "malacca"]}}
    later = store.save_plan(changed)
    assert later["plan_id"] != saved["plan_id"]
    assert store.load_plan(saved["plan_id"]) == saved


def test_tampered_route_file_is_rejected_instead_of_served(tmp_path, monkeypatch):
    from poseidon.routing import store

    monkeypatch.setattr(store, "plans_root", lambda: tmp_path)
    saved = store.save_plan({"distance_nm": 5})
    (tmp_path / (saved["plan_id"] + ".json")).write_text('{"distance_nm":1}')
    with pytest.raises(RoutingError) as error:
        store.load_plan(saved["plan_id"])
    assert error.value.code == "plan_corrupt"


@pytest.mark.parametrize("plan_id", ["../../outside", "../" + "a" * 64, "A" * 64, "a" * 63])
def test_route_identifier_cannot_be_a_path(plan_id, tmp_path, monkeypatch):
    from poseidon.routing import store

    monkeypatch.setattr(store, "plans_root", lambda: tmp_path)
    with pytest.raises(RoutingError) as error:
        store.load_plan(plan_id)
    assert error.value.code == "invalid_plan"


def test_missing_saved_route_is_not_reconstructed_as_another_route(tmp_path, monkeypatch):
    from poseidon.routing import store

    monkeypatch.setattr(store, "plans_root", lambda: tmp_path)
    with pytest.raises(RoutingError) as error:
        store.load_plan("f" * 64)
    assert error.value.code == "plan_not_found"


def test_attachment_spherical_shortlist_does_not_hide_nearer_wgs84_node():
    from poseidon.routing.engine import attachment

    # All sixteen east nodes rank closer on a sphere, but the north node is
    # closest on WGS84 because equatorial meridional and zonal scales differ.
    nodes = [{"lat": 0, "lon": 0.0998 + i * 0.000005} for i in range(16)]
    nodes.append({"lat": 0.1, "lon": 0})
    adjacency = graph(17, [(i, i, 16, 1, None) for i in range(16)])
    port = {"lat": 0, "lon": 0, "name": "reference"}
    attached = attachment(port, {"nodes": nodes}, adjacency, set())
    assert attached["attachment"]["node_id"] == 16
    assert attached["gap_nm"] == pytest.approx(5.9705333126, abs=1e-9)


@pytest.fixture
def saved_route_request(monkeypatch):
    from functools import lru_cache
    from types import SimpleNamespace
    from poseidon.api import routing_voyage
    from poseidon.routing.engine import MANDATORY_BLOCKED, POLICY_VERSION

    @lru_cache(maxsize=1)
    def water_only():
        return STRtree([])

    coordinates = [{"lat": 0, "lon": 0}, {"lat": 0, "lon": 0.01}]
    plan = {"plan_id": "a" * 64, "waypoints": coordinates,
            "distance_nm": geodesic_pieces([[0, 0], [0.01, 0]])[0],
            "coast": {"files_sha256": {"land.shp": "b" * 64}},
            "screening": {"policy": POLICY_VERSION},
            "constraints": {"blocked_passages": sorted(MANDATORY_BLOCKED)},
            "scope": "network_segment_only", "limitations": ["reference gaps excluded"]}
    request = SimpleNamespace(route_plan_id=plan["plan_id"], source="global",
                              waypoints=[SimpleNamespace(**p, port_id=None) for p in coordinates])
    monkeypatch.setattr(routing_voyage, "load_plan", lambda key: plan)
    monkeypatch.setattr(routing_voyage, "coast_fingerprint", lambda: plan["coast"]["files_sha256"])
    monkeypatch.setattr(routing_voyage, "land_index", water_only)
    return request, plan


@pytest.mark.parametrize("mutation", ["coordinate", "port_identity", "point_count", "regional"])
def test_saved_route_cannot_be_reused_with_different_geometry_or_source(saved_route_request, mutation):
    from fastapi import HTTPException
    from poseidon.api.routing_voyage import resolve_route_plan

    request, _ = saved_route_request
    if mutation == "coordinate":
        request.waypoints[0].lat = 0.001
    elif mutation == "port_identity":
        request.waypoints[0].port_id = "unlocode:KRPUS"
    elif mutation == "point_count":
        request.waypoints.pop()
    else:
        request.source = "regional"
    with pytest.raises(HTTPException) as error:
        resolve_route_plan(request)
    assert error.value.status_code == 422


def test_coastline_replacement_invalidates_saved_route(saved_route_request, monkeypatch):
    from fastapi import HTTPException
    from poseidon.api import routing_voyage

    request, _ = saved_route_request
    monkeypatch.setattr(routing_voyage, "coast_fingerprint", lambda: {"land.shp": "c" * 64})
    with pytest.raises(HTTPException) as error:
        routing_voyage.resolve_route_plan(request)
    assert error.value.status_code == 422


@pytest.mark.parametrize("bad_screen", ["land", "distance"])
def test_saved_route_is_checked_again_before_weather_analysis(saved_route_request, monkeypatch, bad_screen):
    from fastapi import HTTPException
    from poseidon.api import routing_voyage

    request, plan = saved_route_request
    result = (plan["distance_nm"], False) if bad_screen == "land" else (plan["distance_nm"] + 1, True)
    monkeypatch.setattr(routing_voyage, "screen_edge", lambda coordinates, index: result)
    with pytest.raises(HTTPException) as error:
        routing_voyage.resolve_route_plan(request)
    assert error.value.status_code == 422


def test_network_only_analysis_carries_plan_and_cannot_claim_port_deadline_met(saved_route_request, monkeypatch):
    from poseidon.api import voyage

    request, plan = saved_route_request
    monkeypatch.setattr(voyage, "_analyze", lambda request: {
        "schema_version": "voyage-1.2", "limitations": [],
        "scenarios": [{"arrival_constraint": {"status": "met"}}]})
    result = voyage.analyze(request)
    assert result["schema_version"] == "voyage-1.4"
    assert result["route_plan"] == plan
    assert result["scope"] == "network_segment_only"
    assert result["scenarios"][0]["arrival_constraint"]["status"] == "not_applicable"


def test_long_manual_route_is_rejected_while_saved_route_can_preserve_native_vertices():
    from pydantic import ValidationError
    from poseidon.api.voyage import VoyageRequest

    values = {"source": "global", "departure_utc": "2026-09-12T00:00:00Z",
              "waypoints": [{"lat": 0, "lon": i / 100} for i in range(21)],
              "scenarios": [{"speed_kn": 10}]}
    with pytest.raises(ValidationError):
        VoyageRequest(**values)
    assert len(VoyageRequest(**values, route_plan_id="a" * 64).waypoints) == 21


def test_client_cannot_enable_a_mandatorily_excluded_canal():
    from pydantic import ValidationError
    from poseidon.api.routing import PlanRequest

    with pytest.raises(ValidationError):
        PlanRequest(departure_port_id="port:a", arrival_port_id="port:b", allow_panama=True)


@pytest.mark.parametrize("mutation", ["screening_policy", "mandatory_passage"])
def test_changed_route_policy_requires_a_new_plan(saved_route_request, mutation):
    from fastapi import HTTPException
    from poseidon.api.routing_voyage import resolve_route_plan

    request, plan = saved_route_request
    if mutation == "screening_policy":
        plan["screening"]["policy"] = "previous-policy"
    else:
        plan["constraints"]["blocked_passages"].remove("panama")
    with pytest.raises(HTTPException) as error:
        resolve_route_plan(request)
    assert error.value.status_code == 422


@pytest.mark.parametrize("port, code", [
    (None, "unknown_port"),
    ({"name": "unknown coordinates", "lat": None, "lon": None}, "missing_coordinate"),
    ({"name": "conflict", "lat": 0, "lon": 0, "coordinate_conflict": True}, "coordinate_conflict"),
])
def test_unresolved_port_reference_does_not_enter_route_search(port, code):
    from poseidon.api.routing import port_for_plan

    with pytest.raises(RoutingError) as error:
        port_for_plan(port)
    assert error.value.code == code
