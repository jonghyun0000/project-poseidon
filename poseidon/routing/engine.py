"""WGS84 network distance and continuous land intersections along densified geodesics.

Geodesics: Karney (2013), doi:10.1007/s00190-012-0578-z.
Shortest paths: nonnegative Dijkstra search. This is distance on a dated network,
not weather routing, a harbor approach, a clearance calculation, or navigation.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
from functools import lru_cache

import numpy as np
import shapely
from geographiclib.geodesic import Geodesic
from shapely.geometry import LineString

from poseidon.core.config import settings
from poseidon.twin.global_coast import land_index

POLICY_VERSION = "route-land-geodesic-2nm-v1"
MANDATORY_BLOCKED = frozenset({"suez", "panama", "kiel", "corinth", "northwest", "northeast"})
OPTIONAL_PASSAGES = frozenset({"malacca", "gibraltar", "babelmandeb", "dover", "bering", "magellan"})


class RoutingError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def geodesic_pieces(coordinates, spacing_nm=2.0):
    if len(coordinates) < 2 or not math.isfinite(spacing_nm) or spacing_nm <= 0:
        raise RoutingError("invalid_geometry", "항로 선분 형식이 올바르지 않습니다.")
    for lon, lat in coordinates:
        if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 < lat < 90):
            raise RoutingError("invalid_geometry", "항로 좌표가 유효 범위를 벗어났습니다.")
    parts, current, distance_nm = [], [], 0.0
    for a, b in zip(coordinates, coordinates[1:]):
        line = Geodesic.WGS84.InverseLine(a[1], a[0], b[1], b[0])
        if line.s13 < 1e-9:
            continue
        distance_nm += line.s13 / 1852
        n = max(1, math.ceil(line.s13 / (spacing_nm * 1852)))
        for k in range(n + 1):
            p = line.Position(line.s13 * k / n)
            lon, lat = p["lon2"], p["lat2"]
            if current:
                x, y = current[-1]
                if abs(lon - x) > 180:
                    # Split the short, already densified segment at the date line.
                    unwrapped = lon + (360 if lon < x else -360)
                    boundary = 180 if x > 0 else -180
                    f = (boundary - x) / (unwrapped - x) if unwrapped != x else 0.0
                    crossing = y + f * (lat - y)
                    current.append((boundary, crossing))
                    if len(current) >= 2:
                        parts.append(LineString(current))
                    current = [(-boundary, crossing)]
            if not current or (lon, lat) != current[-1]:
                current.append((lon, lat))
    if len(current) >= 2:
        parts.append(LineString(current))
    return distance_nm, parts


def screen_edge(coordinates, index):
    if index is None:
        raise RoutingError("coast_unavailable", "육지 검사 자료가 없어 자동 항로를 계산할 수 없습니다.")
    distance, parts = geodesic_pieces(coordinates)
    if distance <= 0 or not parts:
        return distance, False
    # Prepare the reused land polygons, rather than rebuilding their segment index
    # for each queried line. The full-line predicate still catches small islands.
    shapely.prepare(index.geometries)
    for part in parts:
        candidates = index.query(part)
        if len(candidates) and np.any(shapely.intersects(index.geometries[candidates], part)):
            return distance, False
    return distance, True


def shortest_path(adjacency, start, end, blocked):
    if start == end:
        raise RoutingError("same_endpoint", "두 항구가 같은 항로망 접속점에 연결됩니다. 이 항로망에서 구간을 구분할 수 없습니다.")
    distances, previous = {start: 0.0}, {}
    queue = [(0.0, start)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        if node == end:
            nodes, edges = [end], []
            while nodes[-1] != start:
                parent, edge_id = previous[nodes[-1]]
                edges.append(edge_id)
                nodes.append(parent)
            return distance, nodes[::-1], edges[::-1]
        for other, weight, edge_id, passage in adjacency[node]:
            if passage in blocked:
                continue
            if not math.isfinite(weight) or weight <= 0:
                raise RoutingError("invalid_network", "항로망에 유효하지 않은 거리 가중치가 있습니다.")
            candidate = distance + weight
            if candidate < distances.get(other, math.inf):
                distances[other] = candidate
                previous[other] = node, edge_id
                heapq.heappush(queue, (candidate, other))
    raise RoutingError("no_route", "육지·제외 통로 조건을 만족하는 연결 경로가 없습니다. 직선으로 대체하지 않았습니다.")


def coast_fingerprint():
    paths = [settings.data_root / "static/natural-earth-10m" / ("ne_10m_land" + suffix)
             for suffix in (".shp", ".shx", ".dbf", ".prj")]
    if not all(p.exists() for p in paths):
        raise RoutingError("coast_unavailable", "전 지구 해안선 자료를 찾을 수 없습니다.")
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


@lru_cache(maxsize=2)
def _prepared(network_sha, coast_sha):
    from poseidon.routing.network import load_network
    network = load_network()
    if network["manifest"]["graph_sha256"] != network_sha:
        raise RoutingError("network_changed", "항로망 판본이 바뀌었습니다. 다시 계산하세요.")
    land_index.cache_clear()
    index = land_index()
    if index is None:
        raise RoutingError("coast_unavailable", "육지 검사 자료를 불러오지 못했습니다.")
    # The cache key includes every coastline component, not just an old in-memory tree.
    nodes, edges = network["nodes"], network["edges"]
    cache = settings.data_root / "static/routing" / f"screen-{network_sha[:16]}-{coast_sha[:16]}-{POLICY_VERSION}.npz"
    costs, clear = None, None
    if cache.exists():
        try:
            if hashlib.sha256(cache.read_bytes()).hexdigest() != cache.with_suffix(".sha256").read_text():
                raise ValueError("screen cache checksum mismatch")
            with np.load(cache, allow_pickle=False) as data:
                costs, clear = data["costs"], data["clear"]
                if costs.shape != (len(edges),) or clear.shape != (len(edges),) or clear.dtype != bool or not np.isfinite(costs).all():
                    costs, clear = None, None
        except (OSError, ValueError, KeyError):
            costs, clear = None, None
    if costs is None:
        costs, clear = np.zeros(len(edges)), np.zeros(len(edges), dtype=bool)
        for i, edge in enumerate(edges):
            costs[i], clear[i] = screen_edge(edge["coordinates"], index)
        from poseidon.routing.store import atomic_bytes
        import io
        buffer = io.BytesIO()
        np.savez_compressed(buffer, costs=costs, clear=clear)
        atomic_bytes(cache, buffer.getvalue())
        atomic_bytes(cache.with_suffix(".sha256"), hashlib.sha256(buffer.getvalue()).hexdigest().encode())
    adjacency = [[] for _ in nodes]
    for i, edge in enumerate(edges):
        if clear[i] and costs[i] > 0:
            u, v = edge["u"], edge["v"]
            adjacency[u].append((v, float(costs[i]), i, edge.get("passage")))
            adjacency[v].append((u, float(costs[i]), i, edge.get("passage")))
    return network, adjacency, int(clear.sum())


def prepare_network():
    from poseidon.routing.network import load_network
    network = load_network()
    coast = coast_fingerprint()
    digest = hashlib.sha256(json.dumps(coast, sort_keys=True).encode()).hexdigest()
    # Also force the common coastline cache to reflect a replaced file.
    key = (network["manifest"]["graph_sha256"], digest)
    return (*_prepared(*key), coast)


def attachment(port, network, adjacency, blocked, max_gap_nm=100):
    nodes = network["nodes"]
    # WGS84 metric is bounded below by its minimum curvature radius b²/a.
    # Evaluate candidates until this lower bound exceeds the best exact distance.
    lat, lon = math.radians(port["lat"]), math.radians(port["lon"])
    eligible = [i for i, row in enumerate(adjacency) if any(e[3] not in blocked for e in row)]
    if not eligible:
        raise RoutingError("no_route", "이 조건으로 이용할 수 있는 항로망이 없습니다.")
    a = np.array([[nodes[i]["lat"], nodes[i]["lon"]] for i in eligible]) * math.pi / 180
    spherical = np.sin((a[:, 0] - lat) / 2) ** 2 + math.cos(lat) * np.cos(a[:, 0]) * np.sin((a[:, 1] - lon) / 2) ** 2
    ellipsoid = Geodesic.WGS84
    min_radius = ellipsoid.a * (1 - ellipsoid.f) ** 2
    lower = 2 * np.arcsin(np.sqrt(np.clip(spherical, 0, 1))) * min_radius / 1852
    gap, node_id = math.inf, None
    for i in np.argsort(lower):
        if lower[i] > gap:
            break
        n = eligible[i]
        exact = ellipsoid.Inverse(port["lat"], port["lon"], nodes[n]["lat"], nodes[n]["lon"])["s12"] / 1852
        if (exact, n) < (gap, node_id if node_id is not None else math.inf):
            gap, node_id = exact, n
    if gap > max_gap_nm:
        raise RoutingError("attachment_too_far", f"{port['name']}: 가까운 항로망 접속점이 {gap:.1f} nm 떨어져 있습니다. 연결 한도 {max_gap_nm} nm를 넘었습니다.")
    return {"port": port, "attachment": {**nodes[node_id], "node_id": node_id},
            "gap_nm": gap, "scope": "reference_gap_not_navigated"}


def solve(departure_port, arrival_port, avoid_passages=()):
    blocked = MANDATORY_BLOCKED | set(avoid_passages)
    network, adjacency, clear_count, coast = prepare_network()
    departure = attachment(departure_port, network, adjacency, blocked)
    arrival = attachment(arrival_port, network, adjacency, blocked)
    distance, node_ids, edge_ids = shortest_path(adjacency, departure["attachment"]["node_id"], arrival["attachment"]["node_id"], blocked)
    if distance > 25000:
        raise RoutingError("route_too_long", "계산된 해상 경로가 25,000 nm 한도를 넘었습니다.")
    coordinates = []
    used_passages = set()
    for node, edge_id in zip(node_ids, edge_ids):
        edge = network["edges"][edge_id]
        path = edge["coordinates"] if edge["u"] == node else edge["coordinates"][::-1]
        used_passages.update([edge["passage"]] if edge.get("passage") else [])
        for lon, lat in path:
            p = {"lat": lat, "lon": -180 if lon == 180 else lon}
            if not coordinates or p != coordinates[-1]:
                coordinates.append(p)
    if len(coordinates) < 2 or len(coordinates) > 5000:
        raise RoutingError("route_complexity", "경로의 원래 형상을 유지하면서 처리할 수 있는 지점 수를 넘었습니다.")
    checked_distance, clear = screen_edge([[p["lon"], p["lat"]] for p in coordinates], land_index())
    if not clear or not math.isclose(checked_distance, distance, rel_tol=1e-8, abs_tol=1e-6):
        raise RoutingError("route_integrity", "완성 경로의 육지 교차 또는 거리 정합 검사에 실패했습니다.")
    return {"status": "candidate", "distance_nm": distance, "waypoints": coordinates,
            "departure": departure, "arrival": arrival,
            "network": network["manifest"], "coast": {"files_sha256": coast, "source": "Natural Earth v5.1.1 / 1:10 million"},
            "screening": {"status": "no_land_intersection", "policy": POLICY_VERSION, "geodesic_spacing_nm_max": 2,
                          "accepted_network_edges": clear_count, "total_network_edges": len(network["edges"]),
                          "route_edges": len(edge_ids)},
            "constraints": {"blocked_passages": sorted(blocked), "used_passages": sorted(used_passages), "max_attachment_gap_nm": 100},
            "edge_ids": [network["edges"][i]["id"] for i in edge_ids],
            "scope": "network_segment_only",
            "limitations": ["과거 공개 항로망에서 거리 기준으로 계산한 해상 구간 후보입니다. 현재 통항 가능성·수심·흘수·교통분리대는 검증하지 않았습니다.",
                            "항구 대표점과 항로망 접속점 사이의 간격은 위치 차이이며 항행 경로가 아닙니다. 구간 거리·시간·연료에서 제외됩니다.",
                            "해안선은 1:10 million 지도 축척입니다. 해도 수준의 작은 섬·수로·안전 여유를 보증하지 않습니다.",
                            "수에즈·파나마·킬·코린트 운하 및 북서·북동 극지 통로를 제외했습니다. 나머지 통로의 실시간 운영 상태는 확인하지 않습니다.",
                            "가장 가까운 항로망 노드를 선택했습니다. 반도·방파제의 어느 쪽으로 입출항할 수 있는지는 별도 확인이 필요합니다.",
                            "현재 파랑·해류·연료를 최소화한 경로가 아닙니다. 선택한 해상 경로에 예보를 조회하는 단계입니다."]}
