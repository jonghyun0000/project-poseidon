"""Load the locally pinned Eurostat MARNET graph, preserving source geometry.

Source and derivative data are EUPL-1.2, stored with license and reproducible
builder in ``data/static/routing``. Graph connectivity is not navigability.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

from poseidon.core.config import settings

PINNED_COMMIT = "88a2e568a8e0144d1f5a81c3931a7bc2bcce6901"
PINNED_GRAPH_SHA256 = "e50a60b2bfa866fdcbcf732977daa74b7c0461bed7f69e838af5458d1df6003a"


class NetworkUnavailable(RuntimeError):
    """The pinned source network is missing or fails its integrity contract."""


def _same_position(node: dict, xy: list) -> bool:
    lon = -180.0 if xy[0] == 180.0 else xy[0]
    return node["lon"] == lon and node["lat"] == xy[1]


@lru_cache(maxsize=2)
def _read_network(folder: str, manifest_version: tuple, graph_version: tuple) -> dict:
    del manifest_version, graph_version  # Cache keys invalidate on either file change.
    directory = Path(folder)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest["source_commit"] != PINNED_COMMIT:
        raise ValueError("Unknown upstream revision")
    if manifest["graph_file"] != "network.json" or manifest["graph_sha256"] != PINNED_GRAPH_SHA256:
        raise ValueError("Unknown network representation")
    raw = (directory / "network.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != PINNED_GRAPH_SHA256:
        raise ValueError("Routing network checksum mismatch")
    graph = json.loads(raw)
    if graph["schema_version"] != "marnet-graph-1" or graph["edge_direction"] != "undirected":
        raise ValueError("Unexpected routing topology schema")
    if graph["coordinate_reference_system"] != "EPSG:4326":
        raise ValueError("Unexpected routing CRS")
    nodes, edges = graph["nodes"], graph["edges"]
    if len(nodes) != manifest["node_count"] or len(edges) != manifest["edge_count"]:
        raise ValueError("Routing topology counts disagree")
    ids = set()
    vertices = 0
    passages = set(manifest["passage_edge_counts"])
    for node in nodes:
        lat, lon = node["lat"], node["lon"]
        if not math.isfinite(lat) or not math.isfinite(lon) or abs(lat) > 90 or abs(lon) > 180:
            raise ValueError("Invalid routing node")
    for edge in edges:
        if edge["id"] in ids:
            raise ValueError("Duplicate source edge identity")
        ids.add(edge["id"])
        if not isinstance(edge["u"], int) or not isinstance(edge["v"], int):
            raise ValueError("Invalid edge node reference")
        if not 0 <= edge["u"] < len(nodes) or not 0 <= edge["v"] < len(nodes):
            raise ValueError("Missing edge endpoint")
        coordinates = edge["coordinates"]
        if len(coordinates) < 2:
            raise ValueError("Empty source geometry")
        if not _same_position(nodes[edge["u"]], coordinates[0]) or not _same_position(nodes[edge["v"]], coordinates[-1]):
            raise ValueError("Edge geometry does not terminate at its nodes")
        if edge["passage"] is not None and edge["passage"] not in passages:
            raise ValueError("Unknown maritime passage tag")
        for lon, lat in coordinates:
            if not math.isfinite(lat) or not math.isfinite(lon) or abs(lat) > 90 or abs(lon) > 180:
                raise ValueError("Invalid source geometry coordinate")
        vertices += len(coordinates)
    if vertices != manifest["vertex_count"]:
        raise ValueError("Source geometry vertex count disagrees")
    return {"manifest": manifest, "nodes": nodes, "edges": edges}


def load_network(root: Path | None = None) -> dict:
    """Return the checked graph; callers must treat its cached data as read-only.

    ``nodes`` are indexed ``{lat, lon}`` mappings. Edges contain source feature
    ``id``, endpoint indices ``u/v``, full ``[lon, lat]`` vertices and ``passage``.
    Every edge is undirected. Distances must be computed along all its vertices.
    """
    directory = Path(root) if root is not None else settings.data_root / "static" / "routing"
    try:
        m = (directory / "manifest.json").stat()
        g = (directory / "network.json").stat()
        return _read_network(str(directory.resolve()), (m.st_mtime_ns, m.st_size), (g.st_mtime_ns, g.st_size))
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        raise NetworkUnavailable("해상 항로망이 없거나 원천 무결성 검사에 실패했습니다.") from exc
