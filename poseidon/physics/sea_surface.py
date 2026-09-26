"""해면 실현 — 방향 스펙트럼에서 실제 파면 η(x, y, t)를 만든다.

예보가 내놓는 것은 스펙트럼 적률(Hs, 주기, 파향, 방향 확산)이지 개별 파가
아니다. 그것으로 눈에 보이는 바다를 그리려면 **통계적 실현**을 만들어야 한다 —
같은 스펙트럼을 갖는 무수한 해면 중 하나를 뽑는 것이다.

    η(x, y, t) = ΣΣ a_ij cos(k_i·x − ω_i t + φ_ij)
    a_ij = √(2 · E(f_i, θ_j) · Δf · Δθ)

φ는 균등 난수이고, 중심극한정리로 η는 가우시안 해면이 된다.
**이것은 예측이 아니다.** 어느 파정이 언제 어디 오는지는 예보의 정보가 아니며,
보장되는 것은 통계량(유의파고·평균주기·방향 분포)뿐이다.

근거: Longuet-Higgins (1963) *J. Fluid Mech.* 17, 459-480 (가우시안 해면의
선형 중첩 표현); Longuet-Higgins, Cartwright & Smith (1963) (cos^2s 방향 분포);
Kuik, van Vledder & Holthuijsen (1988) *JPO* 18, 1020-1034 (방향 확산 정의).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from poseidon.physics.constants import G_STANDARD
from poseidon.physics.waves import jonswap_spectrum, wavenumber

Arr = NDArray[np.float64]

SPREAD_S_MAX = 200.0
"""cos^2s 지수의 상한. dspr → 0 이면 s가 발산하므로 막는다."""


def spread_exponent(dspr_deg: float) -> float:
    """방향 확산[deg] → cos^2s 지수 s.

    cos^2s 분포의 1차 방향 모멘트 크기는 r = s/(s+1)이고,
    Kuik의 확산은 σ_θ = √(2(1−r)) 이므로 σ_θ² = 2/(s+1),
    따라서 s = 2/σ_θ² − 1 (σ_θ는 라디안).
    """
    sig = np.radians(max(float(dspr_deg), 1e-3))
    return float(np.clip(2.0 / sig ** 2 - 1.0, 0.0, SPREAD_S_MAX))


def directional_spectrum(
    f: Arr, theta: Arr, hs: float, tp: float, mdir_deg: float, dspr_deg: float,
    *, g: float = G_STANDARD,
) -> Arr:
    """E(f, θ) [m²/Hz/rad] — JONSWAP × cos^2s, 총 에너지를 hs에 맞춘다.

    mdir_deg는 **오는 방향**(진북 0, 시계)이다. 반환 스펙트럼의 θ는
    **가는 방향**(수학각, 동=0, 반시계)으로, 엔진 내부 규약과 같다.
    """
    fp = 1.0 / float(np.clip(tp, 1.0, 30.0))
    e1 = jonswap_spectrum(f, fp, g=g)                      # (nf,)
    s = spread_exponent(dspr_deg)
    # 오는 방향(나침반) → 가는 방향(수학각): grid.to_compass_from 의 역
    to_math = np.radians((270.0 - float(mdir_deg)) % 360.0)
    # 중심으로부터의 각도차를 **최단 방향으로 감싼다**. 감싸지 않으면 θ−θ0 가
    # [0, 2π)로 나와 절반(Δ > π)이 cos(Δ/2) < 0 → 0 으로 잘리고, 분포가
    # 한쪽만 남아 평균파향이 계통적으로 틀어진다.
    dth_c = (theta - to_math + np.pi) % (2.0 * np.pi) - np.pi
    d = np.maximum(np.cos(0.5 * dth_c), 0.0) ** (2.0 * s)
    dth = float(theta[1] - theta[0]) if len(theta) > 1 else 2 * np.pi
    d = d / max(float(d.sum()) * dth, 1e-30)               # ∫D dθ = 1
    e = e1[:, None] * d[None, :]
    # 총 분산을 목표 hs에 맞춘다 (JONSWAP α는 형상만 결정)
    df = np.gradient(f)
    m0 = float((e * df[:, None]).sum() * dth)
    target = (float(hs) / 4.0) ** 2
    return e * (target / m0 if m0 > 1e-30 else 0.0)


def realize_components(
    hs: float, tp: float, mdir_deg: float, dspr_deg: float,
    *, depth: float = 1000.0, n_freq: int = 24, n_dir: int = 12,
    seed: int = 0, f_min: float = 0.04, f_max: float = 0.5,
    g: float = G_STANDARD,
) -> dict[str, Arr | float]:
    """스펙트럼을 이산 파 성분으로 샘플링한다.

    반환 성분은 진폭 a, 파수 k, 각주파수 ω, 진행방향 θ(수학각), 위상 φ다.
    Σ a²/2 = m0 가 정확히 성립하도록 만든다 — 이 항등식이 실현의 검증 기준이다.

    주파수는 기하 격자로 뽑는다. 등간격이면 저주파(에너지가 몰린 곳)의
    해상도가 낮아 실현된 파고가 목표보다 작아진다.
    """
    f = np.geomspace(f_min, f_max, n_freq)
    # 방향 격자를 평균파향에 **정렬**한다. 고정 격자(0,10,...)를 쓰면 분포 중심이
    # 격자점 사이에 놓일 때 이산화가 비대칭해져 복원 파향이 최대 dθ/2 만큼 틀어진다
    # (좁은 분포일수록 심하다). 중심을 격자점에 맞추면 그 오차가 사라진다.
    to_math = np.radians((270.0 - float(mdir_deg)) % 360.0)
    theta = to_math + np.linspace(0.0, 2.0 * np.pi, n_dir, endpoint=False)
    e = directional_spectrum(f, theta, hs, tp, mdir_deg, dspr_deg, g=g)

    df = np.gradient(f)
    dth = 2.0 * np.pi / n_dir
    var = e * df[:, None] * dth                            # 성분별 분산
    amp = np.sqrt(2.0 * np.maximum(var, 0.0))

    sigma = 2.0 * np.pi * f
    k = np.asarray(wavenumber(sigma, float(depth), g=g), dtype=np.float64)

    rng = np.random.default_rng(seed)
    phase = rng.uniform(0.0, 2.0 * np.pi, size=amp.shape)

    ff, tt = np.meshgrid(f, theta, indexing="ij")
    kk = np.broadcast_to(k[:, None], amp.shape)

    # 눈에 띄지 않는 성분을 버려 렌더링 부담을 줄인다. 임계는 **상대값**이다 —
    # 절대 임계를 쓰면 저파고에서 잘리는 비율이 커져 화면 파고가 예보보다 낮아진다.
    # 버린 만큼 남은 진폭을 되올려 m0를 정확히 보존한다: 화면의 유의파고가
    # 예보값과 달라지면 그 화면은 예보를 보여주는 것이 아니다.
    a_max = float(amp.max()) if amp.size else 0.0
    keep = amp > a_max * 1e-3
    if not keep.any():
        keep = amp > 0.0
    kept = amp[keep]
    m0_target = (float(hs) / 4.0) ** 2
    m0_kept = float((kept ** 2 / 2.0).sum())
    scale = np.sqrt(m0_target / m0_kept) if m0_kept > 1e-30 else 0.0
    kept = kept * scale
    return {
        "amp": kept, "k": kk[keep], "theta": tt[keep],
        "omega": (2.0 * np.pi * ff)[keep], "phase": phase[keep],
        "f": ff[keep],
        "m0_target": m0_target,
        "m0_realized": float((kept ** 2 / 2.0).sum()),
        "n_total": int(amp.size), "n_kept": int(keep.sum()),
        "energy_kept": float(m0_kept / m0_target) if m0_target > 1e-30 else 1.0,
    }


def elevation(comp: dict, x: Arr, y: Arr, t: float) -> Arr:
    """η(x, y, t) [m]. x는 동쪽, y는 북쪽 방향 거리[m].

    θ는 파가 **가는** 방향(수학각)이므로 파수 벡터는 (k cosθ, k sinθ)다.
    """
    a, k, th = comp["amp"], comp["k"], comp["theta"]
    w, ph = comp["omega"], comp["phase"]
    kx = k * np.cos(th)
    ky = k * np.sin(th)
    xs = np.asarray(x, dtype=np.float64)[..., None]
    ys = np.asarray(y, dtype=np.float64)[..., None]
    return (a * np.cos(kx * xs + ky * ys - w * t + ph)).sum(axis=-1)


def encounter_angle(mdir_deg: float, heading_deg: float) -> float:
    """조우각 [deg]. 0 = 정선수에서 파를 받음(head sea), 180 = 선미(following).

    mdir_deg는 파가 **오는** 방향, heading_deg는 선수 방위(둘 다 진북 0, 시계).
    IMO MSC.1/Circ.1228이 서프라이딩 위험 구간으로 지목하는 것은
    135° < α < 225°(선미파)이고, 파라메트릭 롤은 head/following 양쪽에서 난다.
    """
    return float((float(mdir_deg) - float(heading_deg) + 180.0) % 360.0 - 180.0)
