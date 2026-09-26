import numpy as np
import pytest
from poseidon.api.raster import upsample_sea

pytestmark = pytest.mark.unit


def test_coastal_constant_field_does_not_darken():
    hs = np.array([[0., 4.], [0., 4.]])
    values, alpha = upsample_sea(hs, hs > 0, 4)
    np.testing.assert_allclose(values[alpha > 0], 4.)
    assert np.isfinite(values).all()
    assert (alpha[:, 0] == 0).all()
