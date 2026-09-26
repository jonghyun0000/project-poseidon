"""스펙트럼 이산화 (ADR-002): 주파수 로그 분포 × 방향 등간격.

E(f,θ) [m²/Hz/rad] 컨벤션. 주파수 적분 가중치는 기하 격자의 빈 경계
[f/√r, f√r]에서 유도: df_i = f_i (√r − 1/√r).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

Arr = NDArray[np.float64]


@dataclass(frozen=True)
class SpectralGrid:
    f0: float = 0.05          # 최저 주파수 [Hz]
    ratio: float = 1.1        # 기하 증분 (WAM/SWAN 표준)
    nf: int = 32
    ntheta: int = 36

    f: Arr = field(init=False)
    df: Arr = field(init=False)
    theta: Arr = field(init=False)
    dtheta: float = field(init=False)

    def __post_init__(self) -> None:
        f = self.f0 * self.ratio ** np.arange(self.nf)
        df = f * (np.sqrt(self.ratio) - 1.0 / np.sqrt(self.ratio))
        th = np.linspace(0.0, 2.0 * np.pi, self.ntheta, endpoint=False)
        object.__setattr__(self, "f", f)
        object.__setattr__(self, "df", df)
        object.__setattr__(self, "theta", th)
        object.__setattr__(self, "dtheta", 2.0 * np.pi / self.ntheta)

    # E 배열 규약: (..., nf, ntheta)
    def total_energy(self, e: Arr) -> Arr:
        """m0 = ∬ E df dθ."""
        return (e * self.df[:, None]).sum(axis=(-2, -1)) * self.dtheta

    def hs(self, e: Arr) -> Arr:
        return 4.0 * np.sqrt(np.maximum(self.total_energy(e), 0.0))

    def spectrum_1d(self, e: Arr) -> Arr:
        """E(f) = ∫ E dθ."""
        return e.sum(axis=-1) * self.dtheta

    def peak_frequency(self, e: Arr) -> Arr:
        """포물선 보간 첨두 주파수 (로그 주파수 좌표)."""
        e1 = self.spectrum_1d(e)
        i = np.argmax(e1, axis=-1)
        i = np.clip(i, 1, self.nf - 2)
        lo = np.take_along_axis(e1, (i - 1)[..., None], -1)[..., 0]
        mi = np.take_along_axis(e1, i[..., None], -1)[..., 0]
        hi = np.take_along_axis(e1, (i + 1)[..., None], -1)[..., 0]
        denom = lo - 2 * mi + hi
        good = np.abs(denom) > 1e-30
        # np.where는 두 가지를 모두 평가한다. 나눗셈을 그대로 두면 에너지 0인
        # 셀에서 0/0 경고가 매 스냅숏 쏟아진다(값은 아래 where가 버린다).
        shift = np.where(good, 0.5 * (lo - hi) / np.where(good, denom, 1.0), 0.0)
        shift = np.clip(shift, -0.5, 0.5)
        return self.f[i] * self.ratio ** shift

    def mean_sigma_k(self, e: Arr, g: float) -> tuple[Arr, Arr]:
        """WAM 평균량 (심수): σ̃ = E_tot/∬(E/σ), k̃ = [∬E k^{-1/2}/E_tot]^{-2}."""
        sig = 2.0 * np.pi * self.f
        k = sig ** 2 / g
        w = self.df[:, None] * self.dtheta
        etot = np.maximum((e * w).sum(axis=(-2, -1)), 1e-30)
        inv_sig = (e / sig[:, None] * w).sum(axis=(-2, -1))
        sig_m = etot / np.maximum(inv_sig, 1e-30)
        rk = (e / np.sqrt(k)[:, None] * w).sum(axis=(-2, -1)) / etot
        k_m = 1.0 / np.maximum(rk, 1e-30) ** 2
        return sig_m, k_m

    def moments(self, e: Arr) -> dict[str, Arr]:
        """저장·보간용 **선형** 스펙트럼 모멘트.

        m_n = ∬ E f^n df dθ,  a1 = ∬ E cosθ df dθ,  b1 = ∬ E sinθ df dθ

        다섯 량 모두 E에 선형이다. 그래서 (i) 에너지가 없는 셀(육지 포함)에서
        정확히 0이고, (ii) 공간 보간과 유도가 교환된다 —
        보간(유도(E)) 대신 유도(보간(E))를 써도 같은 값이 나온다.
        파향·주기를 스칼라로 저장하면 이 성질이 깨진다: 각도의 산술 평균은
        359°와 1°의 평균을 180°로 만들고, 첨두 주기는 모드 선택이라 선형이
        아니다. 검증 콜로케이션이 육지 셀의 값을 섞어 허구를 만든 사고
        (CLAUDE.md §6-1)가 파향에서 재현되지 않도록, 보간 대상은 스칼라가
        아니라 이 모멘트여야 한다.

        m0는 `total_energy`를 그대로 호출한다. 같은 값을 다른 연산 순서로
        다시 계산하면 hs가 최하위 비트에서 움직일 수 있기 때문이다.

        근거: Holthuijsen (2007) *Waves in Oceanic and Coastal Waters* §4.2
        (스펙트럼 모멘트), Kuik et al. (1988) *JPO* 18, 1020–1034 (방향 모멘트).
        """
        w = self.df[:, None] * self.dtheta
        ew = e * w
        f1 = self.f[:, None]
        return {
            "m0": self.total_energy(e),
            "m1": (ew * f1).sum(axis=(-2, -1)),
            "m2": (ew * f1 ** 2).sum(axis=(-2, -1)),
            "a1": (ew * np.cos(self.theta)).sum(axis=(-2, -1)),
            "b1": (ew * np.sin(self.theta)).sum(axis=(-2, -1)),
        }

    def peak_direction(self, e: Arr) -> Arr:
        """첨두 주파수 빈의 방향 1차 모멘트 [rad, 수학각·진행방향].

        NDBC MWD가 전 스펙트럼 평균이 아니라 **첨두 주기 대역의 파향**이므로
        (NDBC 관측 정의), 그와 대조하려면 평균파향과 별도로 필요하다.
        첨두 빈 선택이 비선형이라 이 값은 공간 보간 대상이 아니다 —
        콜로케이션에서 최근접 해양 셀로만 다룬다.
        """
        e1 = self.spectrum_1d(e)
        i = np.argmax(e1, axis=-1)
        row = np.take_along_axis(e, i[..., None, None], -2)[..., 0, :]
        a = (row * np.cos(self.theta)).sum(axis=-1)
        b = (row * np.sin(self.theta)).sum(axis=-1)
        return np.arctan2(b, a)


# --- 모멘트 → 물리량 -------------------------------------------------------
# 저장부(scheduler)와 검증부(validation.collocate)가 **이 함수 하나**를 공유한다.
# 서빙과 검증이 각자 구현을 갖고 있어 생긴 과거 결함(train/serve skew, 서빙 경로의
# 육지 혼입)을 반복하지 않기 위해서다. 규약을 바꾸려면 여기만 고친다.

M0_MIN = 1e-8
"""유도량을 정의하는 최소 m0 [m²]. hs≈0.4 mm 미만은 파랑으로 보지 않는다.

에너지가 없는 셀에서 주기·파향은 정의되지 않는다. 그런 셀에 0이나 그럴듯한
상수를 넣으면 하류가 결손을 인지하지 못한다 — 육지에 hs=0을 넣어 검증이
망가진 사고와 같은 구조다. 여기서는 NaN을 반환해 연산이 전파하도록 한다.
"""


def to_compass_from(theta_math: Arr) -> Arr:
    """수학각·진행방향 [rad] → 나침반각·**오는 방향** [deg true, 시계].

    엔진 내부 θ는 수학 규약(동쪽 0, 반시계, 파가 가는 방향)이고,
    관측(KMA WO·NDBC MWD)과 GFS-Wave DIRPW는 기상 규약(진북 0, 시계,
    파가 오는 방향)이다. 규약을 맞추지 않으면 180° 계통 편차가 그대로
    물리 오차로 오인된다. `wave_cycle._from_compass_from`의 역함수다.
    """
    return (270.0 - np.degrees(theta_math)) % 360.0


def derive(
    m0: Arr, m1: Arr, m2: Arr, a1: Arr, b1: Arr
) -> dict[str, Arr]:
    """선형 모멘트에서 물리량을 유도한다. 에너지 없는 셀은 NaN.

    반환 규약:
      hs    유의파고 [m]              = 4√m0
      tm01  평균 주기 [s]             = m0/m1
      tm02  영점교차 평균 주기 [s]     = √(m0/m2)   ← 국제 검증 표준
      dirm  평균 파향 [deg, 오는 방향] = atan2(b1,a1) 를 나침반각으로
      dspr  방향 확산 [deg]           = √(2(1−r)),  r = √(a1²+b1²)/m0

    tm02를 국제 표준으로 삼는 근거: ECMWF/WMO Lead Centre와 CMEMS가 주기
    검증에 Tm02를 쓰고, NDBC APD(변수 `ta`)가 정확히 √(m0/m2)이다.
    첨두 주기 Tp는 다봉 스펙트럼에서 두 피크 사이를 점프해 불안정하므로
    저장은 하되 게이트로 쓰지 않는다.

    근거: Kuik et al. (1988) *JPO* 18, 1020–1034 (방향 확산의 원형 정의).
    """
    ok = m0 > M0_MIN
    nan = np.full(np.shape(m0), np.nan, dtype=np.float64)

    hs = np.where(ok, 4.0 * np.sqrt(np.maximum(m0, 0.0)), nan)
    tm01 = np.where(ok & (m1 > 0.0), m0 / np.where(m1 > 0.0, m1, 1.0), nan)
    tm02 = np.where(
        ok & (m2 > 0.0), np.sqrt(m0 / np.where(m2 > 0.0, m2, 1.0)), nan
    )

    # r 은 정의상 [0,1]이지만 fp 오차로 1을 넘을 수 있다. 클램프를 제거하면
    # √(음수)가 NaN이 되어 정상 셀까지 오염된다 (CLAUDE.md §6-5와 같은 함정).
    r = np.where(ok, np.sqrt(a1 ** 2 + b1 ** 2) / np.where(ok, m0, 1.0), 0.0)
    r = np.clip(r, 0.0, 1.0)
    dspr = np.where(ok, np.degrees(np.sqrt(2.0 * (1.0 - r))), nan)
    dirm = np.where(ok, to_compass_from(np.arctan2(b1, a1)), nan)

    return {"hs": hs, "tm01": tm01, "tm02": tm02, "dirm": dirm, "dspr": dspr}


def derive_peak(g: SpectralGrid, e: Arr) -> dict[str, Arr]:
    """비선형 **첨두** 진단량. 보간 대상이 아니다.

      tp    첨두 주기 [s]
      dirp  첨두 대역 파향 [deg, 오는 방향]

    둘 다 첨두 빈 선택(argmax)을 거치므로 E에 선형이 아니다. 공간 평균이나
    겹선형 보간이 물리적으로 정당화되지 않으니, 콜로케이션에서는 최근접
    해양 셀 값만 쓴다.

    에너지가 없는 셀을 반드시 걸러야 한다. `peak_frequency`는 그런 셀에서
    조용히 18.18 s를, `arctan2(0,0)`은 0°("동쪽에서 오는 파")를 반환한다 —
    NaN이 아니라 물리적으로 그럴듯한 값이라, 걸러내지 않으면 하류가 결손을
    인지하지 못한다. 육지 hs=0이 검증을 오염시킨 사고와 정확히 같은 구조다.
    """
    m0 = g.total_energy(e)
    ok = m0 > M0_MIN
    nan = np.full(np.shape(m0), np.nan, dtype=np.float64)
    fp = g.peak_frequency(e)
    tp = np.where(ok & (fp > 0.0), 1.0 / np.where(fp > 0.0, fp, 1.0), nan)
    dirp = np.where(ok, to_compass_from(g.peak_direction(e)), nan)
    return {"tp": tp, "dirp": dirp}


SNAPSHOT_VARS = ("hs", "m0", "m1", "m2", "a1", "b1", "tp", "dirp")
"""예보 zarr에 저장하는 진단량. L1·L2 저장부가 이 목록 하나를 공유한다."""


def snapshot(g: SpectralGrid, e: Arr) -> dict[str, Arr]:
    """스냅숏 한 벌 — 선형 모멘트 + 첨두량 + hs.

    hs는 `moments()['m0']`에서 유도할 수 있지만 별도로 담는다. 기존 API와
    대시보드가 `ds.hs`를 직접 읽고 있어 하위 호환이 필요하기 때문이다.
    두 값은 비트 단위로 일치한다(tests/unit/test_wave_moments.py).

    평균 주기·평균 파향·방향 확산은 저장하지 않는다. `derive()`로 언제든
    유도되고, 같은 정보를 두 벌 저장하면 둘이 어긋날 때 어느 쪽이 참인지
    알 수 없게 된다.
    """
    e64 = np.asarray(e, dtype=np.float64)
    out: dict[str, Arr] = {"hs": g.hs(e64)}
    out.update(g.moments(e64))
    out.update(derive_peak(g, e64))
    return out
