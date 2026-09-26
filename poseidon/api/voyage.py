"""Bounded, reproducible waypoint voyage analysis endpoint."""
from functools import lru_cache
from typing import Literal
from uuid import uuid4

import pandas as pd
import xarray as xr
from fastapi import APIRouter, HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from poseidon.core.config import settings
from poseidon.twin.voyage import route_geometry, screening, simulate
from poseidon.twin.performance import fuel_baseline, arrival_constraint
from poseidon.validation.collocate import _grid_sea

router = APIRouter()


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Waypoint(InputModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    name: str = Field(default="", max_length=80)
    port_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9:_-]{1,80}$")


class Scenario(InputModel):
    name: str = Field(default="기준", min_length=1, max_length=80)
    speed_kn: float = Field(ge=1, le=40)
    departure_offset_h: float = Field(default=0, ge=0, le=72)


class FuelCurvePoint(InputModel):
    speed_stw_kn: float = Field(ge=1, le=40)
    fuel_t_day: float = Field(gt=0, le=1000)


class PerformanceProfile(InputModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=5, max_length=500)
    source_kind: Literal["measured", "manufacturer", "user_assumption", "synthetic_example"]
    load_condition: str = Field(min_length=2, max_length=200)
    fuel_type: Literal["hfo", "lfo", "mdo"]
    assume_zero_current: Literal[True]
    rate_scope: Literal["total_at_sea_single_fuel"] = "total_at_sea_single_fuel"
    curve: list[FuelCurvePoint] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def ordered_curve(self):
        if any(b.speed_stw_kn <= a.speed_stw_kn for a, b in zip(self.curve, self.curve[1:])):
            raise ValueError("성능표 속력은 중복 없이 오름차순이어야 합니다.")
        return self


class VoyageRequest(InputModel):
    source: Literal['regional', 'global'] = 'regional'
    name: str = Field(default="항로 분석", min_length=1, max_length=80)
    cycle: str | None = Field(default=None, pattern=r"^\d{8}T\d{2}$")
    departure_utc: AwareDatetime
    waypoints: list[Waypoint] = Field(min_length=2, max_length=5000)
    route_plan_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    scenarios: list[Scenario] = Field(min_length=1, max_length=3)
    hs_threshold_m: float = Field(default=3, gt=0, le=30)
    arrival_deadline_utc: AwareDatetime | None = None
    performance_profile: PerformanceProfile | None = None

    @model_validator(mode="after")
    def manual_route_limit(self):
        if self.route_plan_id is None and len(self.waypoints) > 20:
            raise ValueError("수동 웨이포인트는 최대 20개입니다.")
        return self

    @field_validator("departure_utc", "arrival_deadline_utc")
    @classmethod
    def supported_date(cls, value):
        if value is not None and not 2000 <= value.year <= 2100:
            raise ValueError("시각의 연도는 2000–2100 범위여야 합니다.")
        return value


@lru_cache(maxsize=1)
def _terrain(path: str, stamp: float):
    with xr.open_dataset(path) as ds:
        return ds.z.load()


@lru_cache(maxsize=2)
def _wind(path: str, stamp: float):
    with xr.open_zarr(path, consolidated=False) as ds:
        return ds[["u10", "v10"]].load()


@router.post("/v1/voyage/analyze")
def analyze(request: VoyageRequest) -> dict:
    from poseidon.api.port_voyage import resolve_port_references
    from poseidon.api.routing_voyage import resolve_route_plan
    plan = resolve_route_plan(request)
    references = resolve_port_references(request.waypoints)
    result = _analyze(request)
    if plan:
        result["schema_version"] = "voyage-1.4"
        result["route_plan"] = plan
        result["scope"] = "network_segment_only"
        result["limitations"].extend(plan["limitations"])
        for scenario in result["scenarios"]:
            scenario["arrival_constraint"] = {"status": "not_applicable", "reason": "항만 입출항 구간이 빠진 해상 구간이므로 항만 도착 마감을 판정하지 않습니다."}
    if references:
        result["schema_version"] = "voyage-1.3"
        result["port_references"] = references
        result["limitations"].append(
            "항구 좌표는 항만 목록의 대표 위치이며 입항점·선석·항행 가능 경로가 아닙니다. "
            "출입항 수로와 중간 웨이포인트는 별도로 확인해야 합니다."
        )
    return result


def _analyze(request: VoyageRequest) -> dict:
    if request.source == 'global':
        from poseidon.api.global_voyage import analyze_global
        return analyze_global(request)
    # Resolve dependencies at request time to avoid a circular app/router import.
    from poseidon.api.app import _applicability, _latest_wave_cycle, _wave_forecast, _catalog, _zarr_stamp
    cycle = request.cycle or _latest_wave_cycle()
    ds = _wave_forecast(cycle, "L1")
    wind = None
    wind_info = {"status": "not_available", "source": "NOAA GFS 0.25° / 10 m wind", "cycle": cycle}
    rows = [r for r in _catalog().find_datasets("forcing", settings.domain_name, cycle)
            if r["source_id"] == "noaa-gfs-0p25"]
    if rows:
        try:
            wind = _wind(rows[-1]["uri"], _zarr_stamp(rows[-1]["uri"]))
            wind_info.update(status="available", registered_at=rows[-1].get("created_at"),
                             first=pd.to_datetime(wind.time.values[0], utc=True).isoformat(),
                             last=pd.to_datetime(wind.time.values[-1], utc=True).isoformat())
        except (OSError, ValueError, KeyError):
            wind_info["status"] = "read_failed"
    sea, _ = _grid_sea(ds.latitude.values, ds.longitude.values, "L1")
    waypoints = [p.model_dump() for p in request.waypoints]
    spacing = min(5, min(s.speed_kn for s in request.scenarios))
    try:
        geometry = route_geometry(waypoints, spacing)
        for s in request.scenarios:
            if geometry["distance_nm"] / s.speed_kn > 168:
                raise ValueError("현재 분석 한도는 시나리오당 168시간입니다. 항로를 줄이거나 속력을 확인하세요.")
        check_geometry = route_geometry(waypoints, 1)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    terrain_path = settings.data_root / "static" / "etopo2022_60s_east_asia.nc"
    terrain = _terrain(str(terrain_path), terrain_path.stat().st_mtime) if terrain_path.exists() else None
    check = screening(check_geometry, terrain)
    now = pd.Timestamp.now(tz="UTC")
    departure = pd.Timestamp(request.departure_utc).tz_convert("UTC")
    scenarios = []
    for s in request.scenarios:
        start = departure + pd.Timedelta(hours=s.departure_offset_h)
        result = simulate(ds, sea, geometry, start, s.speed_kn, request.hs_threshold_m,
                          lambda lat, lon, hs, lead: _applicability("hs", lat, lon, hs, lead), wind=wind)
        result.update(name=s.name, departure_offset_h=s.departure_offset_h)
        if check["status"] == "land_detected":
            result["status"] = "invalid_route"
        result["fuel"] = fuel_baseline(
            request.performance_profile.model_dump() if request.performance_profile else None,
            s.speed_kn, result["summary"]["duration_h"], invalid_route=check["status"] == "land_detected")
        result["arrival_constraint"] = arrival_constraint(
            result["arrival_utc"], request.arrival_deadline_utc,
            invalid_route=check["status"] == "land_detected")
        scenarios.append(result)
    valid = pd.to_datetime(ds.valid_time.values, utc=True)
    return {
        "analysis_id": str(uuid4()), "schema_version": "voyage-1.1", "created_at_utc": now.isoformat(),
        "request": request.model_dump(mode="json"), "cycle": cycle, "level": "L1",
        "produced_at": ds.attrs.get("produced_at"), "corrected": False,
        "engine": {k: v if isinstance(v, (int, float)) else str(v) for k, v in ds.attrs.items()},
        "units": {"distance": "nautical_mile", "speed": "knot_SOG", "course": "degree_true_COG",
                  "wave_direction": "degree_true_from", "relative_wave_deg": "wave_from minus COG, [-180,180)",
                  "wind_speed": "m/s at 10 m", "wind_direction": "degree_true_from",
                  "fuel": "metric_tonne", "fuel_rate": "metric_tonne/day",
                  "co2": "metric_tonne combustion CO2", "arrival_margin": "hour"},
        "forecast_period": {"first": valid[0].isoformat(), "last": valid[-1].isoformat(),
                            "covers_now": bool(valid[0] <= now <= valid[-1])},
        "distance_nm": geometry["distance_nm"], "legs_nm": geometry["legs_nm"],
        "screening": check, "scenarios": scenarios, "environment": {"wind": wind_info, "current": {"status": "not_available"}},
        "methods": {
            "geometry": "WGS84 ellipsoid / GeographicLib 2.1 / Karney 2013",
            "motion": "constant speed over ground, instantaneous waypoint turns",
            "hs": "sea-normalized spatial, linear temporal, physics raw",
            "mean_variables": "linear moments interpolated before deriving mean quantities",
            "peak_variables": "maximum wet-weight corner, nearest valid time (ties earlier)",
            "exposure": "piecewise-linear Hs over fully covered intervals only",
            "wind": "GFS earth-relative 10 m u/v, bilinear space and linear time; apparent=wind minus vessel SOG vector",
            "fuel": "optional supplied STW-fuel curve, no extrapolation; zero-current calm-water baseline only",
            "sample_spacing_nm_max": spacing, "sample_interval_h_max": 1,
        },
        "limitations": [
            "항행 가능한 항로의 승인·추천 결과가 아닙니다. 항로는 사용자가 입력한 경로입니다.",
            "SOG 일정 가정의 도착 예정시각입니다. 해류·기상 감속·선회·입출항 대기는 계산하지 않습니다.",
            "연료는 제공 곡선과 무해류 가정의 해상 항해 기준값입니다. 바람·파랑 저항, 출항 전 대기·항내 연료는 제외합니다.",
            "연소 CO₂만 계산합니다. 전 과정 온실가스·CII·EEOI 인증·선체 운동·항로 최적화는 포함하지 않습니다.",
            "풍속은 GFS 10 m 예보이며 갑판 높이 보정·돌풍·풍저항은 계산하지 않습니다.",
            "파고 비교선은 사용자 분석 기준이며 선박 안전 한계가 아닙니다.",
            "L1 0.25° 예보를 조밀하게 조회해도 원자료의 공간·시간 해상도가 높아지지 않습니다.",
        ],
    }
