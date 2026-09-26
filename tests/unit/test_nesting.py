import numpy as np

from poseidon.scheduler.nested_cycle import (
    SW_MIN,
    bilinear_weights,
    interp_cells,
    interp_field,
)


def _src():
    src_la = np.arange(30, 35.1, 1.0)
    src_lo = np.arange(120, 126.1, 1.0)
    rng = np.random.default_rng(0)
    e = rng.random((len(src_la), len(src_lo), 2, 3)).astype(np.float32)
    return src_la, src_lo, e


def test_bilinear_interp_exact_on_nodes_and_midpoints():
    src_la, src_lo, e = _src()
    la = np.array([31.5, 33.0])
    lo = np.array([121.25, 124.0])
    j0, i0, wy, wx = bilinear_weights(src_la, src_lo, la, lo)
    out = interp_field(e, j0, i0, wy, wx)
    assert np.allclose(out[1, 1], e[3, 4])                 # 격자점 일치
    ref = (0.5 * 0.75 * e[1, 1] + 0.5 * 0.25 * e[1, 2]
           + 0.5 * 0.75 * e[2, 1] + 0.5 * 0.25 * e[2, 2])
    assert np.allclose(out[0, 0], ref, atol=1e-6)          # 겹선형 가중
    assert out.dtype == np.float32


def test_all_sea_mask_equals_plain_bilinear():
    """전부 해양이면 해상 가중 정규화 = 기존 순수 겹선형."""
    src_la, src_lo, e = _src()
    la = np.array([31.5, 33.0, 34.2])
    lo = np.array([121.25, 124.0, 125.7])
    j0, i0, wy, wx = bilinear_weights(src_la, src_lo, la, lo)
    sea = np.ones((len(src_la), len(src_lo)), dtype=bool)
    plain = interp_field(e, j0, i0, wy, wx)
    masked = interp_field(e, j0, i0, wy, wx, sea)
    assert np.allclose(plain, masked, atol=1e-6)
    assert masked.dtype == np.float32


def test_land_stencil_renormalizes_to_sea_points_only():
    """육지가 섞인 스텐실은 해상점만으로 정규화 (육지 E=0 희석 제거)."""
    src_la, src_lo, e = _src()
    sea = np.ones((len(src_la), len(src_lo)), dtype=bool)
    sea[1, 1] = False                     # (30+1°, 120+1°) 을 육지로
    e = e.copy()
    e[~sea] = 0.0                         # regional.py 의 육지 처리 재현
    la = np.array([31.5])
    lo = np.array([121.25])
    j0, i0, wy, wx = bilinear_weights(src_la, src_lo, la, lo)
    out = interp_field(e, j0, i0, wy, wx, sea)
    w = {(1, 1): 0.5 * 0.75, (1, 2): 0.5 * 0.25, (2, 1): 0.5 * 0.75, (2, 2): 0.5 * 0.25}
    num = sum(wt * e[c] for c, wt in w.items() if sea[c])
    den = sum(wt for c, wt in w.items() if sea[c])
    assert np.allclose(out[0, 0], num / den, atol=1e-6)
    # 희석되던 순수 겹선형보다 반드시 크다 (에너지 결손 회복)
    plain = interp_field(e, j0, i0, wy, wx)
    assert np.all(out[0, 0] >= plain[0, 0] - 1e-9)
    assert out[0, 0].sum() > plain[0, 0].sum()


def test_full_land_stencil_is_zero():
    """스텐실 4점이 모두 육지(또는 sea-weight ≤ 임계)면 0."""
    src_la, src_lo, e = _src()
    sea = np.ones((len(src_la), len(src_lo)), dtype=bool)
    sea[1:3, 1:3] = False
    e = e.copy()
    e[~sea] = 0.0
    j0, i0, wy, wx = bilinear_weights(src_la, src_lo, np.array([31.5]), np.array([121.25]))
    out = interp_field(e, j0, i0, wy, wx, sea)
    assert np.all(out == 0.0)


def test_sea_weight_threshold_cuts_marginal_stencils():
    """sea-weight가 임계 이하인 스텐실은 증폭하지 않고 0으로 버린다."""
    src_la, src_lo, e = _src()
    sea = np.ones((len(src_la), len(src_lo)), dtype=bool)
    sea[1, 1] = sea[1, 2] = sea[2, 1] = False      # 해상은 (2,2) 하나뿐
    e = e.copy()
    e[~sea] = 0.0
    # 목표점을 (2,2)에서 멀리 두어 그 가중치를 SW_MIN 아래로 (wy=wx=0.02 → 4e-4)
    la = np.array([src_la[1] + 0.02])
    lo = np.array([src_lo[1] + 0.02])
    j0, i0, wy, wx = bilinear_weights(src_la, src_lo, la, lo)
    assert wy[0] * wx[0] < SW_MIN
    assert np.all(interp_field(e, j0, i0, wy, wx, sea) == 0.0)


def test_interp_cells_matches_interp_field_subset():
    """링 셀 전용 경로(interp_cells)가 전체 필드 보간과 동일한 값을 낸다."""
    src_la, src_lo, e = _src()
    sea = np.ones((len(src_la), len(src_lo)), dtype=bool)
    sea[2, 2] = sea[3, 1] = False
    e = e.copy()
    e[~sea] = 0.0
    la = np.linspace(30.2, 34.8, 7)
    lo = np.linspace(120.3, 125.6, 9)
    j0, i0, wy, wx = bilinear_weights(src_la, src_lo, la, lo)
    full = interp_field(e, j0, i0, wy, wx, sea)
    ring = np.zeros((len(la), len(lo)), dtype=bool)
    ring[0, :] = ring[-1, :] = ring[:, 0] = ring[:, -1] = True
    ry, rx = np.nonzero(ring)
    sub = interp_cells(e, j0[ry], i0[rx], wy[ry], wx[rx], sea)
    assert sub.shape == (ry.size, 2, 3)
    assert np.allclose(sub, full[ry, rx], atol=1e-6)


def test_chunking_is_value_invariant():
    """청크 분할 경로가 값에 영향을 주지 않는다 (대규모 필드 경로 검증)."""
    src_la = np.arange(30, 40.1, 1.0)
    src_lo = np.arange(120, 130.1, 1.0)
    rng = np.random.default_rng(3)
    e = rng.random((len(src_la), len(src_lo), 4, 5)).astype(np.float32)
    sea = rng.random((len(src_la), len(src_lo))) > 0.3
    e[~sea] = 0.0
    la = np.linspace(30.1, 39.9, 40)
    lo = np.linspace(120.1, 129.9, 40)
    j0, i0, wy, wx = bilinear_weights(src_la, src_lo, la, lo)
    J0, I0 = np.meshgrid(j0, i0, indexing="ij")
    WY, WX = np.meshgrid(wy, wx, indexing="ij")
    ref = interp_cells(e, J0, I0, WY, WX, sea)
    one = np.stack([interp_cells(e, J0[k], I0[k], WY[k], WX[k], sea)
                    for k in range(len(la))])
    assert np.allclose(ref, one, atol=1e-7)
