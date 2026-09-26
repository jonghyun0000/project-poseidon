"""Month boundaries and invalid/future observations must not hide valid stations."""
import pandas as pd
import pytest

from poseidon.api.observations import latest_observations

pytestmark = pytest.mark.unit


def test_month_boundary_qc_and_latest(tmp_path):
    base = tmp_path / "2026"
    base.mkdir()
    def write(month, rows):
        pd.DataFrame(rows, columns=["station_id", "ts", "var", "value", "qc_flag"]).to_parquet(
            base / f"{month:02d}.parquet", index=False)
    write(8, [("A", "2026-08-31T20:00:00Z", "hs", 1.2, 0),
              ("B", "2026-08-31T23:00:00Z", "hs", 2.0, 0),
              ("old", "2026-08-28T23:00:00Z", "hs", 3.0, 0)])
    write(9, [("B", "2026-09-01T00:00:00Z", "hs", 2.2, 0),
              ("B", "2026-09-01T01:00:00Z", "hs", 9.0, 1),
              ("A", "2026-09-01T01:00:00Z", "hs", float("nan"), 0),
              ("future", "2026-09-02T01:00:00Z", "hs", 5.0, 0)])
    result = latest_observations(tmp_path, "hs", pd.Timestamp("2026-09-01T02:00:00Z"), 48)
    assert result.set_index("station_id")["value"].to_dict() == {"A": 1.2, "B": 2.2}
    assert str(result["ts"].dt.tz) == "UTC"


def test_no_partitions(tmp_path):
    assert latest_observations(tmp_path, "hs", pd.Timestamp("2026-09-01T02:00:00Z"), 48).empty
