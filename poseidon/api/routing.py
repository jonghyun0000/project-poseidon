"""Bounded, reproducible sea-network candidates. Harbor connectors remain unverified."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from poseidon.ports.catalog import CatalogUnavailable, catalog_snapshot
from poseidon.routing.engine import MANDATORY_BLOCKED, RoutingError, solve
from poseidon.routing.network import NetworkUnavailable, load_network
from poseidon.routing.store import load_plan, save_plan

router = APIRouter()


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    departure_port_id: str = Field(pattern=r"^[A-Za-z0-9:_-]{1,80}$")
    arrival_port_id: str = Field(pattern=r"^[A-Za-z0-9:_-]{1,80}$")
    avoid_passages: list[Literal["malacca", "gibraltar", "babelmandeb", "dover", "bering", "magellan"]] = Field(default_factory=list, max_length=6)


def port_for_plan(port):
    if port is None:
        raise RoutingError("unknown_port", "항만 목록에 없는 식별자입니다.")
    if port["lat"] is None or port["lon"] is None:
        raise RoutingError("missing_coordinate", f"{port['name']}: 원천 좌표가 없어 자동 항로를 만들 수 없습니다.")
    if port.get("coordinate_conflict") or port.get("coordinate_status") == "conflict":
        raise RoutingError("coordinate_conflict", f"{port['name']}: 원천 좌표가 불일치합니다. 위치가 확인된 다른 항목을 선택하세요.")
    return port


def build_plan(request):
    if request.departure_port_id == request.arrival_port_id:
        raise RoutingError("same_port", "출발항과 도착항을 다르게 선택하세요.")
    meta, ports = catalog_snapshot([request.departure_port_id, request.arrival_port_id])
    result = solve(port_for_plan(ports.get(request.departure_port_id)), port_for_plan(ports.get(request.arrival_port_id)), request.avoid_passages)
    result.update(schema_version="sea-route-1.0", created_at_utc=datetime.now(timezone.utc).isoformat(),
                  request=request.model_dump(),
                  port_catalog={key: meta[key] for key in ("catalog_id", "catalog_sha256", "sources")})
    return save_plan(result)


@router.get("/v1/routing/meta")
def routing_meta():
    try:
        return {"status": "available", "network": load_network()["manifest"], "mandatory_blocked_passages": sorted(MANDATORY_BLOCKED),
                "scope": "network_segment_only"}
    except NetworkUnavailable as exc:
        return {"status": "unavailable", "message": str(exc)}


@router.post("/v1/routing/plan")
def plan_route(request: PlanRequest):
    try:
        return build_plan(request)
    except (CatalogUnavailable, NetworkUnavailable) as exc:
        raise HTTPException(503, str(exc)) from exc
    except RoutingError as exc:
        raise HTTPException(503 if exc.code in ("coast_unavailable", "network_changed") else 422, str(exc)) from exc


@router.get("/v1/routing/plans/{plan_id}")
def stored_route(plan_id: str):
    try:
        return load_plan(plan_id)
    except RoutingError as exc:
        raise HTTPException(404 if exc.code == "plan_not_found" else 422, str(exc)) from exc
