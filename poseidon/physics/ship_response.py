"""선체 응답과 IMO 위험 판정.

**이것은 유체역학 해석이 아니다.** 실제 선체 응답은 선형 포텐셜 이론(스트립
이론·패널법)으로 선체 형상에서 풀어야 하고, 그러려면 우리에게 없는 오프셋
데이터가 필요하다. 여기서 계산하는 것은 두 가지다.

1. **IMO MSC.1/Circ.1228 (2007)의 판정식.** 선장이 악천후에서 위험 상황을
   피하도록 IMO가 제시한 지침이며, 선박 길이·속력·롤 고유주기와 파랑 조건만으로
   판정한다. 이건 근사가 아니라 지침 그대로다.
2. **1자유도 공진 응답.** 롤을 감쇠 진동자로 보고 조우주파수에서의 배율을
   낸다. 정성적 거동(공진 위치·대역폭)은 옳지만 **절대 진폭은 신뢰할 수 없다.**

절대 응답량이 필요하면 선급의 유체역학 해석을 써야 한다. 이 모듈이 답하는 것은
"지금 조건이 IMO가 지목한 위험 구역에 들어가는가"이다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from poseidon.physics.constants import G_STANDARD


@dataclass(frozen=True)
class Ship:
    """선박 제원. 최소한만 받는다 — 더 받아도 쓸 수 있는 물리가 없다."""

    length_m: float = 200.0          # 수선간장 L
    beam_m: float = 32.0             # 폭 B
    draft_m: float = 11.0            # 흘수 d
    gm_m: float = 1.2                # 횡메타센터 높이 GM
    speed_kn: float = 14.0           # 대수속력
    bridge_height_m: float = 25.0    # 선교 시선 높이 (수면 기준)

    @property
    def speed_ms(self) -> float:
        return self.speed_kn * 0.514444

    @property
    def roll_period_s(self) -> float:
        """롤 고유주기 T_R [s].

        IMO 지침의 근사식을 쓴다 (MSC.1/Circ.1228 §3.2, IS Code 기반):
            T_R = 2 C B / √GM,
            C = 0.373 + 0.023 (B/d) − 0.043 (L/100)
        GM이 작을수록 주기가 길어진다(텐더). 실제 T_R은 관성반경에 따라
        달라지므로 이 값은 **추정**이다.
        """
        c = 0.373 + 0.023 * (self.beam_m / self.draft_m) - 0.043 * (self.length_m / 100.0)
        return float(2.0 * c * self.beam_m / np.sqrt(max(self.gm_m, 0.05)))


def encounter_period(tw: float, heading_deg: float, wave_from_deg: float,
                     speed_ms: float, g: float = G_STANDARD) -> float:
    """조우주기 T_e [s].

        ω_e = ω − (ω²/g) V cos μ

    μ는 파의 **진행 방향**과 선박 진행 방향 사이의 각이다. 정면파(파가 선수에서
    오는 경우) μ = 180°이므로 cos μ = −1이 되어 ω_e > ω(조우가 빨라진다).
    선미파는 ω_e < ω다.

    선미파에서 ω_e가 0에 가까워지면 파와 함께 달리는 상태(서프라이딩)이고,
    이때 조우주기는 발산한다. 그 경우 큰 값으로 잘라 반환한다.
    """
    w = 2.0 * np.pi / max(float(tw), 0.5)
    # 조우각: 파가 오는 방향 − 선수방위. 0 = 정면파
    alpha = np.radians((float(wave_from_deg) - float(heading_deg) + 180.0) % 360.0 - 180.0)
    mu = np.pi - alpha          # 파 진행방향과 선박 진행방향 사이 각
    we = w - (w ** 2 / g) * float(speed_ms) * np.cos(mu)
    if abs(we) < 2.0 * np.pi / 600.0:
        return 600.0
    return float(2.0 * np.pi / abs(we))


def roll_magnification(te: float, tr: float, zeta: float = 0.10) -> float:
    """1자유도 감쇠 공진 배율. te ≈ tr 에서 최대.

    R = 1 / √((1 − r²)² + (2ζr)²),  r = T_R / T_e

    ζ는 임계감쇠비다. 선박 횡동요의 등가 감쇠는 통상 0.05~0.15이며
    빌지킬·안정기 유무에 크게 좌우된다 — 기본값 0.10은 **대표값이지
    특정 선박의 값이 아니다.**
    """
    r = float(tr) / max(float(te), 0.1)
    return float(1.0 / np.sqrt((1.0 - r ** 2) ** 2 + (2.0 * zeta * r) ** 2))


def imo_hazards(ship: Ship, hs: float, tp: float, tm02: float,
                wave_from_deg: float, heading_deg: float,
                g: float = G_STANDARD) -> list[dict]:
    """MSC.1/Circ.1228 의 위험 판정. 해당하는 항목만 돌려준다.

    원문: https://wwwcdn.imo.org/localresources/en/OurWork/Safety/Documents/Stability/MSC.1-CIRC.1228.pdf
    §4.2.2의 Hs > 0.04L은 모든 판정의 공통 게이트가 아니다.
    주기 근접 허용폭 ±20%는 이 프로그램의 표시 근사이며 IMO 수치가 아니다.
    Tp를 대표 주기로 사용하므로 실제 선박 거동의 확정 판정은 아니다.
    """
    out: list[dict] = []
    if not all(np.isfinite(v) for v in (hs, tp, tm02, wave_from_deg, heading_deg)):
        raise ValueError("wave inputs must be finite")
    if hs <= 0 or tp <= 0 or tm02 <= 0:
        return out
    L = ship.length_m
    alpha = (float(wave_from_deg) - float(heading_deg) + 180.0) % 360.0 - 180.0
    a_abs = abs(alpha)
    tr = ship.roll_period_s
    te_p = encounter_period(tp, heading_deg, wave_from_deg, ship.speed_ms, g)
    lam = 1.56 * float(tp) ** 2          # 심해 파장 λ = gT²/2π ≈ 1.56 T²

    # (1) §4.2.2.1의 파고 조건만 표시. 전체 위험 판정의 전제가 아니다.
    if hs > 0.04 * L:
        out.append({
            "id": "high_wave", "level": "warn",
            "title": "연속 고파랑의 파고 조건 해당",
            "detail": f"Hs {hs:.1f} m > 0.04·L = {0.04*L:.1f} m. 파장·조우주기와 "
                      "실제 위험 거동도 확인해야 하며 이 조건만으로 위험을 확정하지 않는다",
            "imo": "MSC.1/Circ.1228 §4.2.2.1",
        })

    # (2) 서프라이딩·브로칭 — 선미파에서 선속이 파속에 근접
    if 135.0 < a_abs <= 180.0:
        c_wave = 1.8 * np.sqrt(L) / np.cos(np.radians(180.0 - a_abs))  # §4.2.1 [kn]
        if ship.speed_kn > c_wave:
            out.append({
                "id": "surf_riding", "level": "bad",
                "title": "서프라이딩·브로칭 위험",
                "detail": f"선미파(조우각 {alpha:+.0f}°)에서 선속 {ship.speed_kn:.0f} kn "
                          f"> 임계 {c_wave:.1f} kn — 감속하거나 침로를 바꾼다",
                "imo": "MSC.1/Circ.1228 §4.2.1",
            })
        else:
            out.append({
                "id": "following_sea", "level": "warn",
                "title": "선미파",
                "detail": f"조우각 {alpha:+.0f}° — 조타 응답이 둔해진다. "
                          f"임계 선속 {c_wave:.1f} kn 미만은 유지 중",
                "imo": "MSC.1/Circ.1228 §4.2.1",
            })

    # (3) 동조 횡동요 — 조우주기가 롤 고유주기에 근접
    if 0.85 * tr <= te_p <= 1.15 * tr:
        out.append({
            "id": "synchronous_roll", "level": "bad",
            "title": "동조 횡동요",
            "detail": f"조우주기 {te_p:.1f} s ≈ 롤 고유주기 {tr:.1f} s — "
                      f"공진 배율 약 {roll_magnification(te_p, tr):.1f}배. ±20% 근접 폭은 표시 근사",
            "imo": "MSC.1/Circ.1228 §3.2",
        })

    # (4) 파라메트릭 롤 — 조우주기가 고유주기의 절반
    if 0.4 * tr <= te_p <= 0.6 * tr or 0.8 * tr <= te_p <= 1.2 * tr:
        out.append({
            "id": "parametric_roll", "level": "bad",
            "title": "파라메트릭 롤 위험",
            "detail": f"조우주기 {te_p:.1f} s가 고유주기 {tr:.1f} s 또는 절반에 근접. "
                      "근접 허용폭 ±20%는 표시 근사이며 안정성 변화는 계산하지 않았다",
            "imo": "MSC.1/Circ.1228 §3.3 / §4.2.3.2",
        })

    # (5) 파장이 선박 길이에 가까움 — 종운동이 커진다
    if 0.6 * L <= lam <= 2.3 * L:
        out.append({
            "id": "wavelength_match", "level": "warn",
            "title": "복원성 감소와 관련된 파장 범위",
            "detail": f"파장 {lam:.0f} m / 선박 {L:.0f} m = {lam/L:.2f} — "
                      "파정이 선체 중앙에 있을 때 복원성 감소 가능. 실제 복원성은 계산하지 않았다",
            "imo": "MSC.1/Circ.1228 §3.1.2",
        })
    return out


def motion_estimate(ship: Ship, hs: float, tp: float, tm02: float,
                    wave_from_deg: float, heading_deg: float,
                    dspr_deg: float = 30.0,
                    g: float = G_STANDARD) -> dict:
    """선체 운동의 **정성적** 추정.

    롤은 조우주파수의 공진 배율로, 히브·피치는 파장 대 선박 길이의 필터로
    낸다. **절대 각도·변위는 신뢰할 수 없다** — 실제 값은 선체 형상·적재·
    안정기에 좌우되며 유체역학 해석이 필요하다. 상대적 크기와 어느 조건에서
    커지는지를 보는 용도다.
    """
    alpha = (float(wave_from_deg) - float(heading_deg) + 180.0) % 360.0 - 180.0
    a = np.radians(alpha)
    tr = ship.roll_period_s
    te = encounter_period(tp, heading_deg, wave_from_deg, ship.speed_ms, g)
    lam = 1.56 * float(tp) ** 2
    kl = 2.0 * np.pi * ship.length_m / max(lam, 1.0)

    # 파경사 (유의) — 롤 강제력의 크기 규모
    steep = float(hs) / max(lam, 1.0)
    # 횡 성분: 횡파에서 최대, 정면·선미파에서 최소. 다만 실제 바다는 방향이
    # 퍼져 있어 순수 정면파에서도 횡 강제가 0이 아니다. 확산 σ를 1차로 넣는다 —
    # 이것을 빼면 정면·선미파에서 롤이 정확히 0으로 나와 "안전하다"로 읽힌다.
    sig = np.radians(max(float(dspr_deg), 1.0))
    beam = float(np.sqrt(np.sin(a) ** 2 + (sig * np.cos(a)) ** 2))
    roll_deg = float(np.degrees(steep * 2.6) * roll_magnification(te, tr) * beam)

    # 히브·피치: 파장이 선박보다 짧으면 선체가 평균해 응답이 줄어든다
    filt = float(np.exp(-((kl / (2.0 * np.pi)) ** 2) * 0.6))
    heave_m = float(0.5 * hs * filt)
    pitch_deg = float(np.degrees(steep * 3.2) * filt * abs(np.cos(a)))

    # 파라메트릭 롤은 횡방향 직접 강제가 아니라 종파에서 복원력(GM)이 주기적으로
    # 변해 생긴다. 그래서 조우각이 0/180°일 때 오히려 위험한데, 위의 beam 항은
    # 그 조건에서 최소가 된다. 두 값을 함께 내보내 오해를 막는다.
    para = bool(0.4 * tr <= te <= 0.6 * tr) and bool(0.8 * ship.length_m <= lam <= 2.0 * ship.length_m)

    return {
        "encounter_period_s": round(te, 2),
        "roll_period_s": round(tr, 2),
        "parametric_risk": para,
        "roll_amp_deg": round(min(roll_deg, 45.0), 2),
        "pitch_amp_deg": round(min(pitch_deg, 15.0), 2),
        "heave_amp_m": round(heave_m, 2),
        "roll_magnification": round(roll_magnification(te, tr), 2),
        "wavelength_m": round(lam, 1),
        "encounter_angle_deg": round(alpha, 1),
        "basis": ("1자유도 공진 + 파장 필터 근사. 절대값은 신뢰할 수 없고 "
                  "조건별 상대 크기만 의미가 있다. roll_amp_deg 는 파에 의한 "
                  "**직접** 횡강제만 담는다 — 파라메트릭 롤(복원력 변동)은 "
                  "여기 포함되지 않으므로 parametric_risk 를 함께 보아야 한다"),
    }
