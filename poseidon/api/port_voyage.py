"""Preserve catalog provenance without interpreting a port point as a safe approach."""
from math import isclose

from fastapi import HTTPException


def resolve_port_references(waypoints):
    selected = [(index, p) for index, p in enumerate(waypoints) if p.port_id]
    if not selected:
        return []
    from poseidon.ports.catalog import CatalogUnavailable, catalog_snapshot

    try:
        meta, ports = catalog_snapshot([p.port_id for _, p in selected])
        if meta.get("status") == "unavailable":
            raise CatalogUnavailable("항만 목록을 불러올 수 없습니다.")
        references = []
        for index, waypoint in selected:
            port = ports.get(waypoint.port_id)
            if port is None:
                raise HTTPException(422, f"항만 목록에 없는 항구 ID: {waypoint.port_id}")
            if port.get("lat") is None or port.get("lon") is None:
                raise HTTPException(422, "좌표가 없는 항구는 웨이포인트로 사용할 수 없습니다.")
            if not (isclose(waypoint.lat, port["lat"], abs_tol=1e-6, rel_tol=0)
                    and isclose(waypoint.lon, port["lon"], abs_tol=1e-6, rel_tol=0)):
                raise HTTPException(422, "항구 ID와 좌표가 다릅니다. 항구를 다시 선택하거나 항구 연결을 제거하세요.")
            # Names and source claims are resolved on the server, never trusted from a form.
            waypoint.name = port["name"][:80]
            references.append({
                "waypoint_index": index, "port_id": port["id"],
                "catalog_id": meta.get("catalog_id"),
                "catalog_sha256": meta.get("catalog_sha256"),
                "name": port["name"], "country_code": port["country_code"],
                "unlocode": port.get("unlocode"), "wpi_id": port.get("wpi_id"),
                "lat": port["lat"], "lon": port["lon"],
                "coordinate_source": port.get("coordinate_source"),
                "coordinate_conflict": port.get("coordinate_conflict"),
                "unlocode_status": port.get("unlocode_status"),
                "sources": port.get("sources", []), "reference_only": True,
                "source_snapshots": [source for source in meta.get("sources", [])
                                     if source["id"] in {row["id"] for row in port.get("sources", [])}],
            })
        return references
    except CatalogUnavailable as exc:
        raise HTTPException(503, "항만 목록을 불러올 수 없습니다. 잠시 뒤 다시 시도하세요.") from exc
