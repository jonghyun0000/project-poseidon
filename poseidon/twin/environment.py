"""GFS 10 m earth-relative wind along a moving vessel, without extrapolation."""
import numpy as np
import pandas as pd

from poseidon.validation.collocate import corner_weights

KNOT_MS = 1852.0 / 3600.0


def sample_wind(ds, lat, lon, when, sog_kn, course_deg):
    out = {"status": "not_available", "u10_ms": None, "v10_ms": None,
           "speed_ms": None, "from_deg": None, "apparent_speed_ms": None,
           "apparent_from_deg": None}
    if ds is None or not {"u10", "v10"} <= set(ds):
        return out
    lats, lons = ds.latitude.values, ds.longitude.values
    if not (lats[0] <= lat <= lats[-1] and lons[0] <= lon <= lons[-1]):
        out["status"] = "outside_domain"
        return out
    times = pd.to_datetime(ds.time.values, utc=True).asi8
    t = when.value
    if t < times[0] or t > times[-1]:
        out["status"] = "outside_forecast_time"
        return out
    hi = min(int(np.searchsorted(times, t)), len(times)-1)
    lo = hi if times[hi] == t else max(0, hi-1)
    f = (t-times[lo])/(times[hi]-times[lo]) if hi != lo else 0
    jj, ii, w = corner_weights(lats, lons, lat, lon)
    values = []
    for name in ("u10", "v10"):
        a = ds[name].transpose("time", "latitude", "longitude").values[
            np.array([lo, hi])[:, None], jj, ii]
        # Atmospheric wind is continuous across the coastline; do not apply sea mask.
        v = a @ w
        values.append(float(v[0]*(1-f)+v[1]*f))
    u, v = values
    if not np.isfinite(values).all():
        out["status"] = "missing_values"
        return out
    speed = float(np.hypot(u, v))
    course = np.radians(course_deg)
    au, av = u-sog_kn*KNOT_MS*np.sin(course), v-sog_kn*KNOT_MS*np.cos(course)
    apparent = float(np.hypot(au, av))
    def wind_from(east, north, magnitude):
        return float(np.degrees(np.arctan2(-east, -north)) % 360) if magnitude > 1e-8 else None
    return {"status": "ok", "u10_ms": u, "v10_ms": v, "speed_ms": speed,
            "from_deg": wind_from(u, v, speed), "apparent_speed_ms": apparent,
            "apparent_from_deg": wind_from(au, av, apparent)}


def wind_summary(points):
    valid = [p["wind"]["speed_ms"] for p in points if p["wind"]["status"] == "ok"]
    covered = sum(b["elapsed_h"]-a["elapsed_h"] for a, b in zip(points, points[1:])
                  if a["wind"]["status"] == b["wind"]["status"] == "ok")
    duration = points[-1]["elapsed_h"]
    return {"max_wind_ms": max(valid) if valid else None,
            "wind_coverage_pct": 100*covered/duration if duration else 0}
