import pandas as pd

from poseidon.ingest.adapters.ndbc import parse_realtime2_txt

SAMPLE = """\
#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft
2026 08 03 15 20 360  2.0  4.0   2.4    10   7.5 311 1010.2  14.0  15.5  12.7   MM   MM    MM
2026 08 03 15 10 350  4.0  5.0    MM    MM    MM  MM 1010.2  14.0  15.4  12.6   MM   MM    MM
"""


def test_parse_realtime2_txt_long_format():
    df = parse_realtime2_txt(SAMPLE, "46042")
    assert set(df.columns) == {"station_id", "ts", "var", "value"}
    assert (df["station_id"] == "NDBC:46042").all()

    hs = df[df["var"] == "hs"]
    assert len(hs) == 1  # 두 번째 행은 MM(결측)이라 제외
    assert hs["value"].iloc[0] == 2.4
    assert hs["ts"].iloc[0] == pd.Timestamp("2026-08-03 15:20", tz="UTC")

    slp = df[df["var"] == "slp"]
    assert len(slp) == 2  # 기압은 두 행 모두 유효
    assert (df[df["var"] == "tp"]["value"] == 10.0).all()
