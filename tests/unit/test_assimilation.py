"""관측 동화(oi-hs-v1) 단위 테스트.

게이트 항목: 항등성 · 양수성 · 질량(Hs) 정합 · 단일 관측 영향 반경 · 육지 셀 불변.
이 프로젝트에서 육지 마스크 결함이 네 번 반복됐다 — 육지·경계 관련 단정문은
어떤 이유로도 완화하지 않는다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from poseidon.assimilation import oi, spectral
from poseidon.assimilation.bias import station_bias_loco
from poseidon.assimilation.cycle import assim_attrs, assimilate_initial_state
from poseidon.assimilation.observations import Observation, cycle_to_timestamp
from poseidon.assimilation.params import AssimConfig
from poseidon.engines.spectral_wave.grid import SpectralGrid
from poseidon.engines.spectral_wave.regional import RegionalWaveModel

pytestmark = pytest.mark.unit

CYCLE = "20260821T00"


def make_model(ny: int = 25, nx: int = 25, island: bool = True):
    """소형 시험 도메인: 0.25°, 34~40°N / 128~134°E, 심해 + (옵션) 중앙 남북 육지 띠."""
    lats = 34.0 + 0.25 * np.arange(ny)
    lons = 128.0 + 0.25 * np.arange(nx)
    depth = np.full((ny, nx), 1000.0)
    if island:
        depth[:, nx // 2] = 0.0                 # 남북으로 관통하는 육지 띠
    grid = SpectralGrid()
    return RegionalWaveModel(lats, lons, depth, grid, h_min=10.0)


def make_state(model, hs=1.0, tp=8.0, mdir=0.0):
    ny, nx = model.ny, model.nx
    return model.spectra_from_integrals(np.full((ny, nx), hs),
                                        np.full((ny, nx), tp),
                                        np.full((ny, nx), mdir))


def obs_at(model, j, i, value, sid="TEST:0"):
    return Observation(station_id=sid, lat=float(model.lats[j]), lon=float(model.lons[i]),
                       value=float(value), n_reports=3, provider="test")


def bare_cfg(**kw):
    """편차 보정·시선제약·평활을 끈 최소 구성 (해석 검증용).

    해석 검증은 영향 반경·감쇠 형상을 보려고 **인위적으로 큰 혁신**을 쓰므로
    조대오차 검사(gross_k)와 자기일관성 검사(chi2_max)를 비활성화한다.
    두 검사 자체는 test_gross_error_* 에서 따로 검증한다.
    """
    base = dict(bias_correction=False, line_of_sight=False, smooth_passes=0,
                gross_k=1e9, chi2_max=1e9)
    base.update(kw)
    return AssimConfig(**base)


# ── 항등성 ────────────────────────────────────────────────────────────────────
def test_no_observations_is_bitwise_identity():
    m = make_model()
    e = make_state(m)
    ea, info = assimilate_initial_state(m, e, CYCLE, np.zeros((m.ny, m.nx)),
                                        np.zeros((m.ny, m.nx)),
                                        cfg=bare_cfg(), observations=[])
    assert info["status"] == "no-obs"
    assert np.array_equal(ea, e)
    assert assim_attrs(info)["assimilation"] == "none"


def test_zero_innovation_is_bitwise_identity():
    """관측이 배경과 정확히 같으면 증분은 0 이고 스펙트럼은 비트 동일해야 한다."""
    m = make_model()
    e = make_state(m, hs=1.3)
    hs_b = m.grid.hs(e.astype(np.float64))
    obs = [obs_at(m, 10, 5, hs_b[10, 5], "A"), obs_at(m, 14, 20, hs_b[14, 20], "B")]
    ea, info = assimilate_initial_state(m, e, CYCLE, np.zeros((m.ny, m.nx)),
                                        np.zeros((m.ny, m.nx)),
                                        cfg=bare_cfg(), observations=obs)
    assert info["status"] == "ok"
    assert info["n_obs_used"] == 2
    assert abs(info["oi"]["incr_abs_max_m"]) < 1e-12
    assert np.array_equal(ea, e)


def test_smoothing_preserves_zero_increment():
    m = make_model()
    lnq = np.zeros((m.ny, m.nx))
    out = oi._smooth121_masked(lnq, m.sea.astype(float))
    assert np.all(out == 0.0)


# ── 육지·경계 ─────────────────────────────────────────────────────────────────
def test_land_cells_untouched():
    m = make_model()
    e = make_state(m)
    obs = [obs_at(m, 12, 8, 2.0)]
    ea, _ = assimilate_initial_state(m, e, CYCLE, np.zeros((m.ny, m.nx)),
                                     np.zeros((m.ny, m.nx)),
                                     cfg=bare_cfg(), observations=obs)
    assert np.all(ea[~m.sea] == 0.0)
    assert np.array_equal(ea[~m.sea], e[~m.sea])


def test_boundary_ring_increment_is_zero():
    m = make_model()
    e = make_state(m)
    hs_b = m.grid.hs(e.astype(np.float64))
    slots, _ = oi.build_obs_slots(m, hs_b, [obs_at(m, 3, 3, 2.5)], bare_cfg())
    _, d_hs, _ = oi.analyse_hs(m, hs_b, slots, bare_cfg())
    assert np.all(d_hs[0, :] == 0.0) and np.all(d_hs[-1, :] == 0.0)
    assert np.all(d_hs[:, 0] == 0.0) and np.all(d_hs[:, -1] == 0.0)
    assert np.all(d_hs[~m.sea] == 0.0)


def test_high_land_fraction_observation_rejected():
    m = make_model()
    e = make_state(m)
    hs_b = m.grid.hs(e.astype(np.float64))
    i_land = m.nx // 2
    o = Observation("LAND:1", float(m.lats[12]), float(m.lons[i_land]), 2.0, 1, "test")
    slots, rejected = oi.build_obs_slots(m, hs_b, [o], bare_cfg())
    assert slots == []
    assert rejected[0]["reason"] in ("land-frac", "all-land")


def test_line_of_sight_blocks_across_land():
    """육지 띠 반대편 격자점은 시선제약으로 갱신되지 않아야 한다."""
    m = make_model()
    e = make_state(m)
    hs_b = m.grid.hs(e.astype(np.float64))
    half = m.nx // 2
    o = obs_at(m, 12, half - 4, 2.0)          # 육지 띠 서쪽 관측
    cfg_los = bare_cfg(line_of_sight=True)
    cfg_free = bare_cfg(line_of_sight=False)
    slots, _ = oi.build_obs_slots(m, hs_b, [o], cfg_los)
    _, d_los, _ = oi.analyse_hs(m, hs_b, slots, cfg_los)
    _, d_free, _ = oi.analyse_hs(m, hs_b, slots, cfg_free)
    east = np.s_[1:-1, half + 1:-1]
    assert np.abs(d_los[east]).max() == 0.0          # 동쪽은 완전 차단
    assert np.abs(d_free[east]).max() > 1e-4         # 제약이 없으면 새어 나간다
    assert np.abs(d_los[1:-1, 1:half]).max() > 1e-4  # 서쪽은 정상 갱신


# ── 단일 관측: 해석해와 영향 반경 ─────────────────────────────────────────────
def test_single_obs_gain_matches_analytic():
    """관측점 격자셀의 증분 = sigma_b^2/(sigma_b^2+sigma_o^2) * d (교과서 OI 이득)."""
    m = make_model(island=False)
    e = make_state(m, hs=1.0)
    hs_b = m.grid.hs(e.astype(np.float64))
    cfg = bare_cfg()
    j, i = 12, 12
    d_true = 0.4
    slots, _ = oi.build_obs_slots(m, hs_b, [obs_at(m, j, i, hs_b[j, i] + d_true)], cfg)
    assert len(slots) == 1
    s = slots[0]
    expect = s.sigma_b ** 2 / (s.sigma_b ** 2 + s.sigma_o ** 2) * d_true
    _, d_hs, _ = oi.analyse_hs(m, hs_b, slots, cfg)
    assert d_hs[j, i] == pytest.approx(expect, rel=1e-6)


def test_single_obs_influence_decays_and_is_compact():
    m = make_model(island=False)
    e = make_state(m, hs=1.0)
    hs_b = m.grid.hs(e.astype(np.float64))
    cfg = bare_cfg(corr_len_km=50.0)                 # 지지반경 150 km
    j, i = 12, 12
    slots, _ = oi.build_obs_slots(m, hs_b, [obs_at(m, j, i, hs_b[j, i] + 0.5)], cfg)
    _, d_hs, diag = oi.analyse_hs(m, hs_b, slots, cfg)
    assert d_hs[j, i] == pytest.approx(np.abs(d_hs).max(), rel=1e-9)   # 최대는 관측점
    row = d_hs[j, i:]
    assert np.all(np.diff(row[row > 0]) < 0)                          # 단조 감소
    r = oi.haversine_km(m.lats[j], m.lons[i],
                        m.lats[:, None] * np.ones((1, m.nx)),
                        np.ones((m.ny, 1)) * m.lons[None, :])
    assert np.all(d_hs[r >= diag["support_km"]] == 0.0)               # 유한 지지


def test_positive_innovation_raises_hs_negative_lowers():
    m = make_model(island=False)
    e = make_state(m, hs=1.0)
    hs_b = m.grid.hs(e.astype(np.float64))
    cfg = bare_cfg()
    for sign in (+1.0, -1.0):
        slots, _ = oi.build_obs_slots(
            m, hs_b, [obs_at(m, 12, 12, hs_b[12, 12] + sign * 0.3)], cfg)
        _, d_hs, _ = oi.analyse_hs(m, hs_b, slots, cfg)
        assert np.sign(d_hs[12, 12]) == sign


def test_analysis_never_negative_and_respects_breaking_limit():
    m = make_model(island=False)
    e = make_state(m, hs=1.0)
    hs_b = m.grid.hs(e.astype(np.float64))
    cfg = bare_cfg()
    slots, _ = oi.build_obs_slots(m, hs_b, [obs_at(m, 12, 12, hs_b[12, 12] - 5.0)], cfg)
    hs_a, _, _ = oi.analyse_hs(m, hs_b, slots, cfg)
    assert hs_a.min() >= 0.0
    assert np.all(hs_a <= 0.73 * m.depth + 1e-9)


# ── 스펙트럼 재척도 ───────────────────────────────────────────────────────────
def test_hs_matches_target_after_rescale():
    m = make_model()
    e = make_state(m, hs=1.2)
    hs_b = m.grid.hs(e.astype(np.float64))
    obs = [obs_at(m, 10, 6, 2.0, "A"), obs_at(m, 16, 18, 0.6, "B")]
    ea, info = assimilate_initial_state(m, e, CYCLE, np.full((m.ny, m.nx), 8.0),
                                        np.zeros((m.ny, m.nx)),
                                        cfg=bare_cfg(), observations=obs)
    assert info["spectral"]["hs_err_max"] < 1e-4
    hs_a = m.grid.hs(ea.astype(np.float64))
    assert np.all(np.isfinite(hs_a)) and hs_a.min() >= 0.0
    assert ea.min() >= 0.0
    assert not np.isnan(ea).any() and not np.isinf(ea).any()
    # 갱신되지 않은 먼 셀은 배경 그대로
    far = (np.abs(hs_a - hs_b) < 1e-12)
    assert far.sum() > 0


def test_energy_mode_scales_shape_invariantly():
    grid = SpectralGrid()
    rng = np.random.default_rng(0)
    e = rng.random((3, 4, grid.nf, grid.ntheta)) * 1e-3
    q = np.full((3, 4), 1.5)
    ea, _ = spectral.apply_increment(e, grid, q, mode="energy", dtype=np.float64)
    assert np.allclose(ea, e * 2.25, rtol=1e-9, atol=0)


def test_rescale_factor_ratio_is_q_squared():
    q = np.array([0.5, 0.8, 1.0, 1.7, 2.0])
    ws = np.array([True, False, True, False, False])
    a, b = spectral.rescale_factors(q, ws, "ecmwf")
    assert np.allclose(a / b, q ** 2)
    assert np.allclose(b[ws], 1.0)                       # 풍파: 형상 보존
    assert np.allclose(b[~ws], np.sqrt(q[~ws]))          # 너울: 파형경사 보존


def test_q_is_clipped():
    m = make_model(island=False)
    e = make_state(m, hs=1.0)
    hs_b = m.grid.hs(e.astype(np.float64))
    hs_a = hs_b * 10.0
    cfg = bare_cfg()
    q = oi.energy_ratio(hs_a, hs_b, m, cfg)
    assert q[m.sea].max() <= cfg.q_max + 1e-12
    assert np.all(q[~m.sea] == 1.0)


def test_tiny_background_cells_are_not_amplified():
    m = make_model(island=False)
    e = make_state(m, hs=1.0)
    hs_b = m.grid.hs(e.astype(np.float64))
    hs_b[5, 5] = 0.0                                     # 에너지 없는 셀
    q = oi.energy_ratio(hs_b + 0.5, hs_b, m, bare_cfg())
    assert q[5, 5] == 1.0


# ── 상관·국지화 함수 ──────────────────────────────────────────────────────────
def test_correlation_shapes():
    r = np.array([0.0, 50.0, 150.0, 400.0])
    for shape in ("soar", "exp", "gauss"):
        c = oi.correlation(r, 150.0, shape)
        assert c[0] == pytest.approx(1.0)
        assert np.all(np.diff(c) < 0)
    with pytest.raises(ValueError):
        oi.correlation(r, 150.0, "bogus")


def test_gaspari_cohn_compact_support():
    s = 450.0
    assert oi.gaspari_cohn(np.array([0.0]), s)[0] == pytest.approx(1.0)
    assert oi.gaspari_cohn(np.array([s]), s)[0] == pytest.approx(0.0, abs=1e-12)
    assert np.all(oi.gaspari_cohn(np.linspace(s, 2 * s, 20), s) == 0.0)
    assert np.all(oi.gaspari_cohn(np.linspace(0, s, 50), s) >= 0.0)


# ── 관측 준비 · 편차 ──────────────────────────────────────────────────────────
def test_cycle_to_timestamp():
    assert cycle_to_timestamp("20260821T06") == pd.Timestamp("2026-08-21T06:00Z")


def test_station_bias_excludes_current_cycle():
    """leave-one-cycle-out: 현재 사이클 표본이 편차 추정에 새면 안 된다."""
    rows = []
    for c in ("C1", "C2", "C3", "C4"):
        rows += [{"cycle": c, "station_id": "S1", "innov": 0.0},
                 {"cycle": c, "station_id": "S2", "innov": 0.0}]
    rows += [{"cycle": "C5", "station_id": "S1", "innov": 9.0},
             {"cycle": "C5", "station_id": "S2", "innov": -9.0}]
    df = pd.DataFrame(rows)
    b, diag = station_bias_loco("C5", innovations=df)
    assert diag["status"] == "ok"
    assert diag["n_cycles_loco"] == 4
    assert b["S1"] == pytest.approx(0.0, abs=1e-12)      # C5 의 9.0 이 새지 않았다
    b_all, _ = station_bias_loco("C9", innovations=df)
    assert abs(b_all["S1"]) > 1.0                        # 포함하면 오염된다


def test_station_bias_removes_only_deviation_not_global_mean():
    """전역 평균 혁신은 남긴다 (초기장의 계통 결함 = 동화 대상)."""
    rows = [{"cycle": c, "station_id": s, "innov": 0.2}
            for c in ("C1", "C2", "C3") for s in ("S1", "S2", "S3")]
    b, diag = station_bias_loco("CX", innovations=pd.DataFrame(rows))
    assert diag["mu_global"] == pytest.approx(0.2)
    assert all(abs(v) < 1e-12 for v in b.values())


def test_station_bias_insufficient_cycles():
    df = pd.DataFrame([{"cycle": "C1", "station_id": "S1", "innov": 0.3}])
    b, diag = station_bias_loco("C1", innovations=df)
    assert b == {} and diag["status"] == "insufficient-cycles"


def test_excluded_station_is_not_assimilated():
    m = make_model(island=False)
    e = make_state(m)
    cfg = bare_cfg(exclude_stations=("A",))
    obs = [obs_at(m, 12, 12, 2.0, "A"), obs_at(m, 14, 14, 2.0, "B")]
    obs = [o for o in obs if o.station_id not in cfg.exclude_stations]
    _, info = assimilate_initial_state(m, e, CYCLE, np.zeros((m.ny, m.nx)),
                                       np.zeros((m.ny, m.nx)),
                                       cfg=cfg, observations=obs)
    assert info["stations"] == ["B"]


def test_attrs_record_method_and_stations():
    m = make_model(island=False)
    e = make_state(m)
    obs = [obs_at(m, 12, 12, 1.5, "A")]
    _, info = assimilate_initial_state(m, e, CYCLE, np.zeros((m.ny, m.nx)),
                                       np.zeros((m.ny, m.nx)),
                                       cfg=bare_cfg(), observations=obs)
    at = assim_attrs(info)
    assert at["assimilation"] == "oi-hs-v1"
    assert at["assim_n_obs"] == 1 and at["assim_stations"] == "A"
    assert "corr_len_km" in at["assim_config"]


# ── 회귀: 실사이클에서 발견된 결함 ────────────────────────────────────────────
def test_boundary_ring_untouched_end_to_end_with_smoothing():
    """평활을 켠 전체 경로에서도 경계 링 Hs 가 정확히 불변이어야 한다.

    회귀: analyse_hs 는 링에서 d_hs=0 을 만들지만 energy_ratio 의 1-2-1 평활이
    내부 ln q 를 링으로 번지게 해 20260821T00 에서 6.8e-4 m 가 샜다. 링은
    Dirichlet 경계이므로 동화가 건드려서는 안 된다.
    """
    m = make_model(island=False)
    e = make_state(m, hs=1.0)
    cfg = AssimConfig(bias_correction=False, line_of_sight=False, smooth_passes=2,
                      gross_k=1e9, chi2_max=1e9)
    # 링 바로 안쪽(j=2,i=2)에 큰 혁신을 둬서 평활이 링으로 번질 조건을 만든다
    obs = [obs_at(m, 2, 2, 3.0, "RING")]
    e_a, info = assimilate_initial_state(m, e, CYCLE, np.zeros((m.ny, m.nx)),
                                         np.zeros((m.ny, m.nx)),
                                         cfg=cfg, observations=obs)
    assert info["status"] == "ok" and info["n_obs_used"] == 1
    hs_b = m.grid.hs(np.asarray(e, dtype=np.float64))
    hs_a = m.grid.hs(np.asarray(e_a, dtype=np.float64))
    d = hs_a - hs_b
    assert np.abs(d).max() > 1e-3, "시험이 무의미해지지 않도록 실제 증분이 있어야 한다"
    for sl in (np.s_[0, :], np.s_[-1, :], np.s_[:, 0], np.s_[:, -1]):
        assert np.array_equal(np.asarray(e_a)[sl], np.asarray(e)[sl])
        assert np.all(d[sl] == 0.0)


def test_out_of_domain_observation_rejected_not_clamped():
    """도메인 밖 관측은 폐기돼야 한다 — corner_weights 가 가장자리로 클램프하므로
    명시적 경계 검사가 없으면 태평양 부이가 도메인 가장자리 셀에 동화된다.
    """
    m = make_model(island=False)
    hs_b = m.grid.hs(make_state(m).astype(np.float64))
    far = Observation(station_id="NDBC:46059", lat=38.0, lon=-129.9,
                      value=1.5, n_reports=1, provider="ndbc")
    slots, rejected = oi.build_obs_slots(m, hs_b, [far], bare_cfg())
    assert slots == []
    assert rejected and rejected[0]["reason"] == "outside-domain"


# ── 조대오차 방어 (F2 회귀) ──────────────────────────────────────────────────
def test_gross_error_observation_is_rejected():
    """지속 offset(생물부착 등)은 상류 QC를 통과하므로 동화가 스스로 막아야 한다.

    실측 근거: 관측 하나를 +5 m 오염시키자 폐기 0건으로 통과해 증분 |max|가
    0.25 → 1.47 m로 폭주하고 인접 관측소 분석값이 0.49 m 끌려갔다.
    """
    m = make_model(island=False)
    e_b = make_state(m, hs=1.0)
    hs_b = m.grid.hs(e_b.astype(float))
    j, i = m.ny // 2, m.nx // 2
    good = obs_at(m, j, i, float(hs_b[j, i]) + 0.15, sid="TEST:good")
    bad = obs_at(m, j + 4, i + 4, float(hs_b[j, i]) + 5.0, sid="TEST:bad")

    cfg = bare_cfg(gross_k=3.5)          # 기본 임계 복원
    slots, _ = oi.build_obs_slots(m, hs_b, [good, bad], cfg)
    _, d_hs, diag = oi.analyse_hs(m, hs_b, slots, cfg)

    rejected = {r["station_id"] for r in diag.get("rejected_gross", [])}
    assert "TEST:bad" in rejected, "5 m 오염 관측이 폐기되지 않았다"
    assert "TEST:good" not in rejected, "정상 관측까지 폐기하면 안 된다"
    assert float(np.abs(d_hs).max()) < 0.5, "폐기 후에도 증분이 폭주한다"


def test_chi2_gate_aborts_assimilation():
    """B/R 예산이 크게 틀린 사이클(chi2 과대)은 동화를 포기한다."""
    m = make_model(island=False)
    e_b = make_state(m, hs=1.0)
    hs_b = m.grid.hs(e_b.astype(float))
    j, i = m.ny // 2, m.nx // 2
    cfg = bare_cfg(gross_k=1e9, chi2_max=1.0)      # 조대오차는 통과시키되 chi2로 차단
    obs = [obs_at(m, j, i, float(hs_b[j, i]) + 2.0, sid="TEST:x")]
    slots, _ = oi.build_obs_slots(m, hs_b, obs, cfg)
    hs_a, d_hs, diag = oi.analyse_hs(m, hs_b, slots, cfg)
    assert diag.get("status") == "rejected-chi2"
    assert float(np.abs(d_hs).max()) == 0.0        # 증분 정확히 0
    assert np.array_equal(hs_a, hs_b)              # 배경장 그대로
