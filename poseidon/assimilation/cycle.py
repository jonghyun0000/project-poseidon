"""동화 1회 실행의 오케스트레이션 — 초기장 E(f,theta) 를 관측으로 교정한다.

    E_b --(grid.hs)--> Hs_b --OI--> Hs_a --(q=Hs_a/Hs_b)--> 재척도 --> E_a

**동화는 물리를 대체하지 않는다.** 초기장만 교정하고 그 이후는 동결된 엔진
(advection=uno2, gse=none — docs/ENGINE_FREEZE.md)이 그대로 전진시킨다.
적용 여부·방법·관측소 목록은 산출물 attrs 에 반드시 각인된다(CLAUDE.md 규칙 3).
"""

from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from poseidon.assimilation import spectral
from poseidon.assimilation.bias import station_bias_loco
from poseidon.assimilation.observations import Observation, load_hs_observations
from poseidon.assimilation.oi import analyse_hs, build_obs_slots, energy_ratio
from poseidon.assimilation.params import METHOD_TAG, AssimConfig

log = logging.getLogger("poseidon.assimilation")


def assimilate_initial_state(
    model, e, cycle: str, u10, udir, *,
    cfg: AssimConfig | None = None,
    observations: list[Observation] | None = None,
    station_bias: dict[str, float] | None = None,
    shallow_stop: bool = True,
):
    """초기 스펙트럼 e 에 Hs 관측을 동화한 (e_a, info) 를 돌려준다.

    관측이 0건이면 e 를 **그대로**(같은 객체가 아닌 사본) 돌려주고
    info["status"]="no-obs" 로 표시한다 — 조용히 실패하지 않는다.
    """
    cfg = cfg or AssimConfig()
    grid = model.grid
    info: dict[str, object] = {
        "method": cfg.method, "cycle": cycle,
        "analysis_time": pd.Timestamp.now("UTC").isoformat(timespec="seconds"),
        "config": cfg.as_attrs(),
    }

    if observations is None:
        observations = load_hs_observations(
            cycle, window_min=cfg.obs_window_min,
            exclude_stations=cfg.exclude_stations)
    info["n_obs_loaded"] = len(observations)

    bias: dict[str, float] = {}
    if cfg.bias_correction:
        if station_bias is not None:
            bias, bdiag = station_bias, {"status": "provided"}
        else:
            bias, bdiag = station_bias_loco(cycle, shrink_k=cfg.bias_shrink_k)
        info["bias_diag"] = bdiag
        if bdiag.get("status") != "ok" and station_bias is None:
            log.warning("관측소 편차 추정 불가(%s) — 편차 보정 없이 진행하되 "
                        "sigma_o 는 보정 전 값으로 되돌린다", bdiag.get("status"))
            cfg = _no_bias(cfg)
            bias = {}
    info["bias_correction"] = bool(cfg.bias_correction)

    hs_b = grid.hs(np.asarray(e, dtype=np.float64))
    slots, rejected = build_obs_slots(model, hs_b, observations, cfg, bias)
    info["n_obs_used"] = len(slots)
    info["n_obs_rejected"] = len(rejected)
    info["rejected"] = rejected
    info["stations"] = [s.station_id for s in slots]

    if not slots:
        info["status"] = "no-obs"
        info["hs_incr_rms_m"] = 0.0
        return np.array(e, copy=True), info

    hs_a, d_hs, oi_diag = analyse_hs(model, hs_b, slots, cfg)
    info["oi"] = oi_diag

    m0_b = grid.total_energy(np.asarray(e, dtype=np.float64))
    q = energy_ratio(hs_a, hs_b, model, cfg, m0_b=m0_b)

    if cfg.rescale == "ecmwf":
        ws_frac = spectral.windsea_fraction(e, grid, np.nan_to_num(u10),
                                            np.nan_to_num(udir),
                                            c_phase=model.c_phase, g=model.g)
        is_ws = ws_frac > cfg.windsea_frac
        ss = (spectral.shallow_stop_mask(e, grid, model.depth, model.g)
              if shallow_stop else None)
        info["windsea_cell_frac"] = float(is_ws[model.sea].mean())
    else:
        is_ws, ss = np.zeros(hs_b.shape, dtype=bool), None
        info["windsea_cell_frac"] = 0.0

    e_a, sp_diag = spectral.apply_increment(
        e, grid, q, is_windsea=is_ws, mode=cfg.rescale,
        shallow_stop=ss, sea=model.sea, dtype=np.asarray(e).dtype)
    info["spectral"] = sp_diag

    hs_final = grid.hs(np.asarray(e_a, dtype=np.float64))
    info.update({
        "status": "ok",
        "hs_incr_rms_m": float(np.sqrt(((hs_final - hs_b)[model.sea] ** 2).mean())),
        "hs_incr_abs_max_m": float(np.abs(hs_final - hs_b).max()),
        "hs_mean_before_m": float(hs_b[model.sea].mean()),
        "hs_mean_after_m": float(hs_final[model.sea].mean()),
        "q_min": float(q[model.sea].min()), "q_max": float(q[model.sea].max()),
    })
    log.info("assimilation %s: %d obs, chi2=%.2f, Hs 증분 RMS %.4f m (최대 %.3f m)",
             cycle, len(slots), float(oi_diag.get("chi2", float("nan"))),
             info["hs_incr_rms_m"], info["hs_incr_abs_max_m"])
    return e_a, info


def _no_bias(cfg: AssimConfig) -> AssimConfig:
    from dataclasses import replace
    return replace(cfg, bias_correction=False)


def assim_attrs(info: dict | None) -> dict[str, object]:
    """zarr attrs 용 평탄화 (문자열/수치만). 동화를 안 했으면 assimilation='none'.

    **왜 attrs 인가**: 파일이 스스로 출처를 말해야 표본 오염을 사후에 가려낼 수 있다
    (CLAUDE.md 함정 9). advection/gse 태그와 같은 방식이다.
    """
    if not info or info.get("status") not in ("ok", "no-obs"):
        return {"assimilation": "none"}
    if info.get("status") == "no-obs":
        return {"assimilation": "none", "assim_attempted": str(info.get("method", METHOD_TAG)),
                "assim_status": "no-obs", "assim_n_obs": 0}
    oi = info.get("oi", {}) or {}
    return {
        "assimilation": str(info.get("method", METHOD_TAG)),
        "assim_status": "ok",
        "assim_time": str(info.get("analysis_time", "")),
        "assim_n_obs": int(info.get("n_obs_used", 0)),
        "assim_n_rejected": int(info.get("n_obs_rejected", 0)),
        "assim_stations": ",".join(info.get("stations", [])),
        "assim_bias_correction": str(bool(info.get("bias_correction", False))),
        "assim_chi2": float(oi.get("chi2", float("nan"))),
        "assim_hs_incr_rms_m": float(info.get("hs_incr_rms_m", 0.0)),
        "assim_hs_incr_max_m": float(info.get("hs_incr_abs_max_m", 0.0)),
        "assim_config": json.dumps(info.get("config", {}), sort_keys=True),
    }
