"""WGS84 waypoint transit and wave exposure. Constant SOG, no route optimization.

Geodesics: Karney (2013), Algorithms for geodesics, J. Geodesy 87, 43–55,
doi:10.1007/s00190-012-0578-z. GeographicLib InverseLine/Position.
UTC transit time is distance / user-specified speed over ground (1 nmi = 1852 m).
No resistance, current, turn dynamics, fuel model, or navigability certification.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import xarray as xr
from geographiclib.geodesic import Geodesic

from poseidon.engines.spectral_wave.grid import derive
from poseidon.validation.collocate import corner_weights, sea_normalized
from poseidon.twin.environment import sample_wind, wind_summary

NMI_M = 1852.0
VARIABLES = ("hs", "tp", "tm02", "dirm", "dirp")


def route_geometry(waypoints: list[dict], spacing_nm: float = 5.0, *, max_distance_nm=3000) -> dict:
    """Densify every leg; include exact endpoints/waypoints and cumulative distance."""
    segments, total = [], 0.0
    for a, b in zip(waypoints, waypoints[1:]):
        line = Geodesic.WGS84.InverseLine(a["lat"], a["lon"], b["lat"], b["lon"])
        distance = line.s13 / NMI_M
        if distance < 0.001:
            raise ValueError("연속 웨이포인트가 같습니다. 중복 지점을 제거하세요.")
        total += distance
        if total > max_distance_nm:
            raise ValueError(f"현재 분석 한도는 총 {max_distance_nm:,.0f}해리입니다.")
        segments.append((line, distance))
    points, done = [], 0.0
    for leg, (line, distance) in enumerate(segments):
        count = max(1, math.ceil(distance / spacing_nm))
        for k in range(count + 1):
            if leg and k == 0:
                continue
            d = distance * k / count
            p = line.Position(d * NMI_M)
            points.append({"lat": p["lat2"], "lon": p["lon2"],
                           "distance_nm": done + d, "course_deg": p["azi2"] % 360,
                           "leg": leg + 1})
        done += distance
    return {"distance_nm": done, "points": points,
            "legs_nm": [d for _, d in segments]}


def sample_weather(ds: xr.Dataset, sea: np.ndarray | None, lat: float, lon: float,
                   when: pd.Timestamp) -> dict:
    """Sea-normalized spatial values, time interpolation without extrapolation.

    Hs follows existing serving/collocation interpolation. Mean quantities are
    derived after interpolating moments in space AND time. Peak quantities use
    the maximum wet-weight corner and nearest forecast time (ties use earlier).
    """
    out = {"values": dict.fromkeys(VARIABLES), "missing": {}, "status": "ok"}
    lats, lons = ds.latitude.values, ds.longitude.values
    if not (lats[0] <= lat <= lats[-1] and lons[0] <= lon <= lons[-1]):
        out["status"] = "outside_domain"
        return out
    times = pd.to_datetime(ds.valid_time.values, utc=True).asi8
    t = when.value
    if t < times[0] or t > times[-1]:
        out["status"] = "outside_forecast_time"
        return out
    if sea is None:
        out["status"] = "mask_unavailable"
        return out
    hi = min(int(np.searchsorted(times, t)), len(times) - 1)
    lo = max(0, hi - 1) if times[hi] != t else hi
    f = (t - times[lo]) / (times[hi] - times[lo]) if hi != lo else 0.0
    jj, ii, weights = corner_weights(lats, lons, lat, lon)
    wet = sea[jj, ii]
    out["land_frac"] = float(np.sum(weights * (1 - wet)))
    if np.sum(weights * wet) < 0.05:
        out["status"] = "land_or_dry"
        return out

    def linear(name):
        corners = ds[name].transpose("lead", "latitude", "longitude").values[
            np.array([lo, hi])[:, None], jj, ii]
        v, _, _ = sea_normalized(corners, weights, wet)
        return float(v[0] * (1 - f) + v[1] * f)

    hs = linear("hs")
    if not np.isfinite(hs) or hs <= 0.011:
        out["status"] = "no_wave_energy"
        return out
    out["values"]["hs"] = hs
    names = ("m0", "m1", "m2", "a1", "b1")
    if set(names) <= set(ds.data_vars):
        mean = derive(**{name: np.asarray(linear(name)) for name in names})
        for name in ("tm02", "dirm"):
            v = float(mean[name])
            out["values"][name] = v if np.isfinite(v) else None
    k = lo if f <= 0.5 else hi
    corner = int(np.argmax(weights * wet))
    for name in ("tp", "dirp"):
        if name in ds:
            v = float(ds[name].isel(lead=k, latitude=jj[corner], longitude=ii[corner]))
            out["values"][name] = v if np.isfinite(v) and (name != "tp" or v > 0) else None
    out["peak_valid_time"] = pd.Timestamp(times[k], tz="UTC").isoformat()
    for name, value in out["values"].items():
        if value is None:
            out["missing"][name] = "not_stored" if name not in ds and (
                name in ("tp", "dirp") or "m0" not in ds) else "no_wave_energy"
    return out


def screening(geometry: dict, terrain: xr.DataArray | None) -> dict:
    """ETOPO nearest-cell land screen on <=1 nmi samples, not a navigation chart."""
    if terrain is None:
        return {"status": "unavailable", "land_count": 0, "unknown_count": len(geometry["points"]),
                "points": [], "note": "지형 자료가 없어 육지 통과 검사를 하지 못했습니다."}
    points = geometry["points"]
    lat = xr.DataArray([p["lat"] for p in points], dims="point")
    lon = xr.DataArray([p["lon"] for p in points], dims="point")
    z = terrain.interp(lat=lat, lon=lon, method="nearest").values
    bad = [dict(lat=p["lat"], lon=p["lon"], leg=p["leg"]) for p, v in zip(points, z)
           if np.isfinite(v) and v >= 0]
    unknown = int(np.sum(~np.isfinite(z)))
    return {"status": "land_detected" if bad else "incomplete" if unknown else "no_land_detected",
            "land_count": len(bad), "unknown_count": unknown, "points": bad[:30],
            "sample_spacing_nm_max": 1,
            "source": "ETOPO 2022 / 60 arcsec / nearest cell",
            "note": "지형 표본 검사입니다. 수심 여유·항로 규제·작은 장애물·공식 해도는 검증하지 않습니다."}


def exposure_summary(points: list[dict], threshold: float) -> dict:
    """Time-weighted exposure only over intervals with two valid Hs endpoints."""
    covered, above, area = 0.0, 0.0, 0.0
    for a, b in zip(points, points[1:]):
        x, y = a["values"]["hs"], b["values"]["hs"]
        if x is None or y is None:
            continue
        dt = b["elapsed_h"] - a["elapsed_h"]
        covered += dt
        area += dt * (x + y) / 2
        if x > threshold and y > threshold:
            above += dt
        elif (x > threshold) != (y > threshold):
            fraction = (threshold - x) / (y - x)
            above += dt * (fraction if x > threshold else 1 - fraction)
    duration = points[-1]["elapsed_h"]
    hs = [p["values"]["hs"] for p in points if p["values"]["hs"] is not None]
    return {"duration_h": duration, "covered_duration_h": covered,
            "coverage_pct": 100 * covered / duration if duration else 0,
            "above_threshold_h": above if covered else None,
            "max_hs_m": max(hs) if hs else None,
            "mean_hs_m": area / covered if covered else None,
            "valid_points": len(hs), "total_points": len(points)}


def simulate(ds, sea, geometry, departure, speed_kn, threshold, applicability=None, *, wind=None):
    points = []
    times = pd.to_datetime(ds.valid_time.values, utc=True)
    for position in geometry["points"]:
        elapsed = position["distance_nm"] / speed_kn
        when = departure + pd.Timedelta(hours=elapsed)
        weather = sample_weather(ds, sea, position["lat"], position["lon"], when)
        p = {**position, **weather, "elapsed_h": elapsed, "valid_time": when.isoformat(),
             "lead_h": (when - times[0]).total_seconds() / 3600}
        direction = p["values"]["dirp"]
        p["relative_wave_deg"] = (direction - p["course_deg"] + 180) % 360 - 180 if direction is not None else None
        p["wind"] = sample_wind(wind, p["lat"], p["lon"], when, speed_kn, p["course_deg"])
        if applicability and p["values"]["hs"] is not None:
            p["applicability"] = applicability(position["lat"], position["lon"],
                                                p["values"]["hs"], p["lead_h"])
        points.append(p)
    summary = exposure_summary(points, threshold)
    summary.update(wind_summary(points))
    return {"departure_utc": departure.isoformat(), "arrival_utc": points[-1]["valid_time"],
            "speed_kn": speed_kn, "summary": summary, "points": points,
            "status": "covered" if summary["coverage_pct"] > 99.999 else "partial"}
