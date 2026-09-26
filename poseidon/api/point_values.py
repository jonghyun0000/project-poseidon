"""지점 다변수 샘플링. 평균량은 모멘트 보간 후 유도, 첨두량은 해양 셀 선택."""

import numpy as np

from poseidon.engines.spectral_wave.grid import derive
from poseidon.validation.collocate import sea_normalized


def finite_value(value):
    return round(float(value), 3) if np.isfinite(value) else None


def sample_variables(ds, jj, ii, weights, sea, requested):
    empty = np.full(ds.sizes["lead"], np.nan)
    result = {v: empty.copy() for v in requested}
    names = ("m0", "m1", "m2", "a1", "b1")
    if set(names) <= set(ds.data_vars):
        moments = {}
        for name in names:
            corners = ds[name].transpose("lead", "latitude", "longitude").values[:, jj, ii]
            moments[name], _, _ = sea_normalized(corners, weights, sea)
        derived = derive(**moments)
        for name in requested:
            if name in derived:
                result[name] = derived[name]
    # 첨두 빈은 비선형이다. 검증부와 같은 최대 해양 보간 가중치 셀 사용.
    wet_weights = weights * sea
    if np.sum(wet_weights) > 0:
        nearest = int(np.argmax(wet_weights))
        for name in ("tp", "dirp"):
            if name in requested and name in ds:
                result[name] = ds[name].transpose("lead", "latitude", "longitude").values[
                    :, jj[nearest], ii[nearest]]
    return result
