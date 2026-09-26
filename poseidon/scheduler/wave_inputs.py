"""GFS-Wave 적분량(swh·perpw·dirpw)을 예보 격자로 옮긴다 — 초기장·경계 공용.

왜 별도 모듈인가 (docs/PHASE28, PHASE31)
    기존 방식은 `xarray.interp`(겹선형)이다. 원천 격자와 우리 격자는 거의 정렬돼 있어
    (위도 일치, 경도 4e-6° 차) 겹선형 가중치가 사실상 (1, 0, 0, 0)이다. 그런데 scipy 의
    겹선형은 가중치 0 인 모서리도 곱하므로 **이웃 모서리가 육지(NaN)면 0×NaN = NaN** 이 된다.
    그 NaN 을 `RegionalWaveModel.spectra_from_integrals` 가 0 으로 바꿔, 해상 셀이 파고 0 으로
    출발했다. 20260920T00 기준 1,287셀 중 381셀이 이 경우다(나머지 906셀은 원천 범위 밖 —
    아래 "하지 않는 것").
    오염에는 방향이 있다: scipy 는 격자점에 정확히 놓인 점에 대해 아래쪽 구간을 고르므로
    가중 0 모서리는 **남쪽** 이웃이고, 경도 어긋남 때문에 **서쪽** 이웃이 가중 1.6e-5 로 들어간다.
    그래서 육지 격자점의 북쪽·동쪽 해상 셀이 오염된다.

모드 — **기존 값이 유한한 셀은 어느 모드에서도 비트 단위로 그대로다.** 바뀌는 것은 기존 NaN 셀뿐이다.
    none         기존 방식 그대로 (운영 기본값)
    seanorm      NaN 셀만 해상 정규화 겹선형: 유한 모서리 가중만 다시 정규화한다.
                 유한 모서리 가중 합이 MIN_WEIGHT 미만이면 채우지 않는다 — 가중이 거의 0 인
                 먼 모서리로 채우는 것은 외삽이고, 그것은 nn1 의 일이다
    seanorm_nn1  seanorm 뒤에도 남은 NaN 셀을, 그 셀에 가장 가까운 원천 격자점의
                 **체비셰프 거리 1 이내 이웃(8방향)** 중 유한한 가장 가까운 점 값으로 채운다.
                 등거리면 평균(방향은 원형 평균). 등거리 허용치는 원천 격자 간격의 1/1000 —
                 원천 경도의 4e-6° 어긋남 때문에 동서 이웃이 0.249996 대 0.250004 로 갈리는
                 좌표 정밀도상의 가짜 차이를 같은 거리로 본다. 1셀 한도는 관행값이며 동료심사 근거는
                 찾지 못했다 [확인 필요]

파향 평균은 원형으로 한다(단위 벡터 가중 평균). 각도를 산술 평균하면 350°와 10°의 평균이
180°가 된다(AGENTS.md §6-7). 기존 방식이 dirpw 를 산술 겹선형 보간하는 것도 같은 결함이지만
**이번 실험 범위 밖이라 고치지 않는다** — 한 번에 하나만 바꾼다(§6-11). PHASE31 에 기록.

하지 않는 것
    원천 범위 밖(예보 도메인 12.5–15°N — 수집 범위 `core.types.EAST_ASIA` 가 15°N 부터다)은
    어느 모드도 채우지 않는다. 그 띠는 초기장만이 아니라 바람과 남쪽 경계까지 비어 있는
    별도 결함(D1)이고, 고치려면 수집 범위를 넓혀야 한다.
"""
from __future__ import annotations

import numpy as np
import xarray as xr

INIT_FILL_MODES = ("none", "seanorm", "seanorm_nn1")
INIT_FILL_VERSION = "v1"
# collocate.MIN_SEA_WEIGHT 와 같은 값 — 검증 경로와 같은 기준으로 "사실상 육지"를 판정한다
MIN_WEIGHT = 0.05


def _bracket(axis: np.ndarray, x: float) -> tuple[int, int, float] | None:
    """오름차순 축에서 x 를 둘러싼 (i0, i1, t). 범위 밖이면 None (외삽하지 않는다)."""
    if not (axis[0] - 1e-9 <= x <= axis[-1] + 1e-9):
        return None
    i1 = int(np.searchsorted(axis, x, side="left"))
    i1 = min(max(i1, 1), len(axis) - 1)
    i0 = i1 - 1
    span = axis[i1] - axis[i0]
    t = float(np.clip((x - axis[i0]) / span, 0.0, 1.0)) if span > 0 else 0.0
    return i0, i1, t


def _circ_mean(deg: np.ndarray, w: np.ndarray) -> float:
    r = np.radians(deg)
    return float(np.degrees(np.arctan2((w * np.sin(r)).sum(), (w * np.cos(r)).sum())) % 360.0)


def grid_wave_fields(ds: xr.Dataset, lats: np.ndarray, lons: np.ndarray,
                     mode: str = "none") -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """(hs, tp, dir_from_deg, info). dir 은 진북 0·시계·오는 방향 [도] — 변환은 호출부가 한다."""
    if mode not in INIT_FILL_MODES:
        raise ValueError(f"unknown init_fill {mode!r} (supported: {INIT_FILL_MODES})")
    i = {"latitude": xr.DataArray(lats, dims="y"), "longitude": xr.DataArray(lons, dims="x")}
    hs = ds.swh.interp(**i).values
    tp = ds.perpw.interp(**i).values
    dr = ds.dirpw.interp(**i).values
    info = {"mode": mode, "nan_before": int(np.isnan(hs).sum()),
            "filled_seanorm": 0, "filled_nn1": 0, "out_of_source": 0}
    if mode == "none":
        return hs, tp, dr, info

    hs, tp, dr = hs.copy(), tp.copy(), dr.copy()
    sla = np.asarray(ds.latitude.values, dtype=float)
    slo = np.asarray(ds.longitude.values, dtype=float)
    if sla[0] > sla[-1] or slo[0] > slo[-1]:
        raise ValueError("source grid must be ascending in latitude and longitude")
    S = ds.swh.transpose("latitude", "longitude").values
    P = ds.perpw.transpose("latitude", "longitude").values
    D = ds.dirpw.transpose("latitude", "longitude").values
    ok = np.isfinite(S) & np.isfinite(P) & np.isfinite(D)
    # 가중치를 곱하기 전에 결측을 0 으로 둔다. 0 가중 × NaN = NaN 이 바로 고치려는 결함이다
    # (첫 구현이 이 실수를 그대로 되풀이했고 단위 테스트가 잡았다 — PHASE31).
    S0, P0, D0 = np.where(ok, S, 0.0), np.where(ok, P, 0.0), np.where(ok, D, 0.0)

    remaining = []
    for y, x in zip(*np.nonzero(np.isnan(hs))):
        by, bx = _bracket(sla, float(lats[y])), _bracket(slo, float(lons[x]))
        if by is None or bx is None:
            info["out_of_source"] += 1          # D1 — 채우지 않는다
            continue
        j0, j1, ty = by
        i0, i1, tx = bx
        jj = np.array([j0, j0, j1, j1]); ii = np.array([i0, i1, i0, i1])
        w = np.array([(1 - ty) * (1 - tx), (1 - ty) * tx, ty * (1 - tx), ty * tx])
        m = ok[jj, ii]
        ws = w * m
        if ws.sum() >= MIN_WEIGHT:
            hs[y, x] = float((ws * S0[jj, ii]).sum() / ws.sum())
            tp[y, x] = float((ws * P0[jj, ii]).sum() / ws.sum())
            dr[y, x] = _circ_mean(D0[jj, ii], ws)
            info["filled_seanorm"] += 1
        else:
            remaining.append((y, x))

    if mode == "seanorm_nn1":
        coslat = np.cos(np.radians(np.clip(sla, -89.0, 89.0)))
        tie_tol = 1e-3 * float(min(np.min(np.diff(sla)), np.min(np.diff(slo))))
        for y, x in remaining:
            jn = int(np.argmin(np.abs(sla - lats[y])))
            inn = int(np.argmin(np.abs(slo - lons[x])))
            cand = []
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    a, b = jn + dj, inn + di
                    if 0 <= a < len(sla) and 0 <= b < len(slo) and ok[a, b]:
                        dist = np.hypot(sla[a] - lats[y], (slo[b] - lons[x]) * coslat[a])
                        cand.append((dist, a, b))
            if not cand:
                continue
            dmin = min(c[0] for c in cand)
            near = [(a, b) for dist, a, b in cand if dist <= dmin + tie_tol]
            aa = np.array([a for a, _ in near]); bb = np.array([b for _, b in near])
            wn = np.ones(len(near))
            hs[y, x] = float(S[aa, bb].mean())
            tp[y, x] = float(P[aa, bb].mean())
            dr[y, x] = _circ_mean(D[aa, bb], wn)
            info["filled_nn1"] += 1
    info["nan_after"] = int(np.isnan(hs).sum())
    return hs, tp, dr, info
