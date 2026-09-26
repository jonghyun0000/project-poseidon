import numpy as np
import pandas as pd
import pytest
import xarray as xr

from poseidon.core.types import Cycle
from poseidon.ingest import global_wave

pytestmark = pytest.mark.unit


def test_same_valid_time_from_wrong_cycle_cannot_masquerade_as_short_lead(monkeypatch):
    # Same valid time, but actually yesterday's +30 h forecast, not today's +6 h.
    ds=xr.Dataset(coords={'latitude':[0.], 'longitude':[0.],
                          'time':np.datetime64('2026-09-10T00'),
                          'step':np.timedelta64(30,'h'),
                          'valid_time':np.datetime64('2026-09-11T06')})
    monkeypatch.setattr(global_wave,'_decode_step',lambda raw:ds)
    cycle=Cycle(pd.Timestamp('2026-09-11T00:00Z').to_pydatetime())
    with pytest.raises(ValueError,match='초기시각 또는 리드'):
        global_wave.decode(b'GRIB',cycle,6)
