"""선체 응답과 IMO 위험 판정.

이 모듈은 유체역학 해석이 아니라 IMO MSC.1/Circ.1228의 판정식을 옮긴 것이다.
그래서 검증도 "지침이 규정한 조건에서 판정이 뜨는가"로 한다.
"""

from __future__ import annotations

import numpy as np
import pytest

from poseidon.physics.ship_response import (
    Ship,
    encounter_period,
    imo_hazards,
    motion_estimate,
    roll_magnification,
)

pytestmark = pytest.mark.unit


def test_encounter_period_shortens_in_head_sea() -> None:
    """정면파는 조우주기가 짧아지고 선미파는 길어진다. 뒤집히면 모든 판정이 틀린다."""
    s = Ship(speed_kn=14.0)
    tw = 10.0
    head = encounter_period(tw, heading_deg=0.0, wave_from_deg=0.0, speed_ms=s.speed_ms)
    beam = encounter_period(tw, heading_deg=90.0, wave_from_deg=0.0, speed_ms=s.speed_ms)
    foll = encounter_period(tw, heading_deg=180.0, wave_from_deg=0.0, speed_ms=s.speed_ms)
    assert head < tw < foll
    assert beam == pytest.approx(tw, rel=0.02)          # 횡파는 선속 영향이 없다


def test_encounter_period_at_zero_speed_equals_wave_period() -> None:
    for hd in (0.0, 45.0, 90.0, 180.0):
        assert encounter_period(9.0, hd, 0.0, 0.0) == pytest.approx(9.0, rel=1e-9)


def test_roll_magnification_peaks_at_resonance() -> None:
    tr = 20.0
    at = roll_magnification(tr, tr)
    off = roll_magnification(tr * 0.5, tr)
    assert at > off * 3.0
    assert at == pytest.approx(1.0 / (2 * 0.10), rel=0.02)   # 공진에서 1/(2ζ)


def test_roll_period_follows_imo_formula() -> None:
    """GM이 작을수록 롤 주기가 길어진다 (텐더 선박)."""
    stiff = Ship(gm_m=3.0).roll_period_s
    tender = Ship(gm_m=0.8).roll_period_s
    assert tender > stiff
    assert 5.0 < stiff < 40.0 and 5.0 < tender < 60.0


def test_synchronous_roll_detected() -> None:
    """조우주기가 롤 고유주기에 맞으면 동조 횡동요를 경고한다."""
    s = Ship()
    hz = imo_hazards(s, hs=7.9, tp=12.1, tm02=8.8, wave_from_deg=180.0, heading_deg=0.0)
    assert any(h["id"] == "synchronous_roll" for h in hz)


def test_parametric_roll_detected_in_longitudinal_sea() -> None:
    s = Ship()
    hz = imo_hazards(s, hs=7.9, tp=12.1, tm02=8.8, wave_from_deg=0.0, heading_deg=0.0)
    assert any(h["id"] == "parametric_roll" for h in hz)


def test_high_wave_threshold_is_length_dependent() -> None:
    """지침의 Hs > 0.04L 은 선박 길이의 함수다 — 상수가 아니다."""
    small, big = Ship(length_m=60.0), Ship(length_m=300.0)
    hs = 5.0
    assert any(h["id"] == "high_wave"
               for h in imo_hazards(small, hs, 10.0, 8.0, 90.0, 0.0))
    assert not any(h["id"] == "high_wave"
                   for h in imo_hazards(big, hs, 10.0, 8.0, 90.0, 0.0))


def test_calm_sea_has_no_hazards() -> None:
    s = Ship()
    assert imo_hazards(s, hs=0.6, tp=5.0, tm02=4.0, wave_from_deg=45.0, heading_deg=0.0) == []


def test_roll_is_nonzero_in_longitudinal_sea_due_to_spread() -> None:
    """정면·선미파에서도 롤이 정확히 0이면 안 된다.

    실제 바다는 방향이 퍼져 있어 횡 강제가 남는다. 0으로 나오면 화면이
    '안전하다'로 읽히는데, 바로 그 조건이 파라메트릭 롤 위험역이다.
    """
    s = Ship()
    m = motion_estimate(s, 7.9, 12.1, 8.8, wave_from_deg=0.0, heading_deg=0.0,
                        dspr_deg=34.0)
    assert m["roll_amp_deg"] > 0.0
    assert m["parametric_risk"] is True


def test_beam_sea_rolls_more_than_longitudinal_at_same_resonance() -> None:
    """같은 조우주기라면 횡파가 종파보다 직접 롤이 크다."""
    s = Ship(speed_kn=0.0)               # 선속 0이면 조우주기가 각도와 무관
    beam = motion_estimate(s, 4.0, 9.0, 7.0, 90.0, 0.0, dspr_deg=25.0)
    head = motion_estimate(s, 4.0, 9.0, 7.0, 0.0, 0.0, dspr_deg=25.0)
    assert beam["encounter_period_s"] == pytest.approx(head["encounter_period_s"], rel=1e-6)
    assert beam["roll_amp_deg"] > head["roll_amp_deg"]


def test_motion_is_finite_and_bounded() -> None:
    s = Ship()
    rng = np.random.default_rng(0)
    for _ in range(200):
        m = motion_estimate(s, float(rng.uniform(0, 15)), float(rng.uniform(3, 20)),
                            float(rng.uniform(2, 16)), float(rng.uniform(0, 360)),
                            float(rng.uniform(0, 360)), dspr_deg=float(rng.uniform(5, 80)))
        assert np.isfinite(m["roll_amp_deg"]) and 0.0 <= m["roll_amp_deg"] <= 45.0
        assert np.isfinite(m["pitch_amp_deg"]) and 0.0 <= m["pitch_amp_deg"] <= 15.0
        assert np.isfinite(m["heave_amp_m"]) and m["heave_amp_m"] >= 0.0
        assert m["encounter_period_s"] > 0.0


def test_surf_riding_uses_imo_speed_and_encounter_angle():
    def surf(speed, angle):
        return any(h['id'] == 'surf_riding' for h in
                   imo_hazards(Ship(length_m=100, speed_kn=speed), 2, 10, 8, angle, 0))
    assert not surf(17.9, 180)
    assert surf(18.1, 180)
    assert not surf(20, 140)  # 사분면 파는 임계가 더 높다.
    assert not surf(40, 135)  # 원문 범위의 경계는 제외.


def test_low_height_does_not_disable_period_matching():
    ship = Ship(speed_kn=0)
    hazards = imo_hazards(ship, 1, ship.roll_period_s, 8, 90, 0)
    assert {'synchronous_roll', 'parametric_roll'} <= {h['id'] for h in hazards}
    assert 'high_wave' not in {h['id'] for h in hazards}


def test_zero_wave_energy_has_no_hazards():
    assert imo_hazards(Ship(), 0, 12, 8, 180, 0) == []
