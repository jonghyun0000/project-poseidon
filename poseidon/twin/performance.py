"""Empirical, calm-water fuel accounting. No weather resistance or current model.

Rates come entirely from an explicitly supplied speed-through-water/fuel curve.
No cubic law or synthetic vessel performance is assumed by the server.
Liquid-fuel combustion CO2 factors: IMO MEPC.364(79), §2.2.1 (2022).
This is not EEDI/EEOI/CII certification or a well-to-wake GHG calculation.
"""
import numpy as np
import pandas as pd

CO2_FACTORS = {"hfo": 3.114, "lfo": 3.151, "mdo": 3.206}
CO2_SOURCE = "IMO MEPC.364(79), section 2.2.1 (2022)"


def fuel_baseline(profile, speed_kn, duration_h, *, invalid_route=False):
    out = {"status": "not_configured", "fuel_t": None, "co2_t": None,
           "fuel_rate_t_day": None, "profile_name": None, "source_kind": None}
    if profile is None:
        return out
    out.update(profile_name=profile["name"], source=profile["source"],
               source_kind=profile["source_kind"], source_verification="user_declared_not_verified",
               load_condition=profile["load_condition"],
               fuel_type=profile["fuel_type"], co2_factor=CO2_FACTORS[profile["fuel_type"]],
               co2_factor_source=CO2_SOURCE,
               scope="calm water, zero current, at-sea duration only; no port/departure-wait fuel",
               method="piecewise-linear supplied STW-fuel curve; fuel=rate*hours/24",
               assumption="SOG equals STW only under explicitly accepted zero-current assumption")
    if invalid_route:
        out["status"] = "invalid_route"
        return out
    speeds = [p["speed_stw_kn"] for p in profile["curve"]]
    rates = [p["fuel_t_day"] for p in profile["curve"]]
    if not speeds[0] <= speed_kn <= speeds[-1]:
        out["status"] = "outside_curve"
        return out
    rate = float(np.interp(speed_kn, speeds, rates))
    fuel = rate*duration_h/24
    out.update(status="synthetic_example" if profile["source_kind"] == "synthetic_example" else "baseline_estimate",
               fuel_rate_t_day=rate, fuel_t=fuel, co2_t=fuel*CO2_FACTORS[profile["fuel_type"]])
    return out


def arrival_constraint(arrival, deadline, *, invalid_route=False):
    if deadline is None:
        return {"status": "not_configured", "deadline_utc": None, "margin_h": None}
    deadline = pd.Timestamp(deadline).tz_convert("UTC")
    if invalid_route:
        return {"status": "invalid_route", "deadline_utc": deadline.isoformat(), "margin_h": None}
    margin = (deadline-pd.Timestamp(arrival)).total_seconds()/3600
    return {"status": "on_time" if margin >= 0 else "late", "deadline_utc": deadline.isoformat(),
            "margin_h": margin, "basis": "constant-SOG ETA; weather/current delay excluded"}
