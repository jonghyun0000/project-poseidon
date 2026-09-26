"""Verify saved network geometry before admitting it to the voyage calculator."""
import math

from fastapi import HTTPException

from poseidon.routing.engine import RoutingError, screen_edge, coast_fingerprint, POLICY_VERSION, MANDATORY_BLOCKED
from poseidon.routing.store import load_plan
from poseidon.twin.global_coast import land_index


def resolve_route_plan(request):
    if not request.route_plan_id:
        return None
    if request.source != "global":
        raise HTTPException(422, "자동 해상 항로의 예보 분석은 전 세계 원천을 선택해야 합니다.")
    try:
        plan = load_plan(request.route_plan_id)
        if plan.get("screening", {}).get("policy") != POLICY_VERSION or not MANDATORY_BLOCKED.issubset(plan.get("constraints", {}).get("blocked_passages", [])):
            raise RoutingError("policy_changed", "항로 제외·육지 검사 정책이 바뀌었습니다. 자동 항로를 다시 계산하세요.")
        expected = plan["waypoints"]
        if len(expected) != len(request.waypoints) or any(
            p.port_id or not math.isclose(p.lat, e["lat"], abs_tol=1e-8, rel_tol=0)
            or not math.isclose(p.lon, e["lon"], abs_tol=1e-8, rel_tol=0)
            for p, e in zip(request.waypoints, expected)
        ):
            raise RoutingError("geometry_mismatch", "저장된 자동 항로와 입력 좌표가 다릅니다. 자동 항로를 다시 불러오거나 수동 항로로 전환하세요.")
        if coast_fingerprint() != plan["coast"]["files_sha256"]:
            raise RoutingError("coast_changed", "해안선 판본이 바뀌었습니다. 자동 항로를 다시 계산하세요.")
        land_index.cache_clear()
        distance, clear = screen_edge([[p["lon"], p["lat"]] for p in expected], land_index())
        if not clear or not math.isclose(distance, plan["distance_nm"], abs_tol=1e-6, rel_tol=1e-8):
            raise RoutingError("invalid_route_plan", "저장 항로의 거리·육지 교차 재검사에 실패했습니다.")
        return plan
    except RoutingError as exc:
        raise HTTPException(422, str(exc)) from exc
