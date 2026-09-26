from poseidon.ingest.adapters.kma import parse_obs, parse_station_info

BUOY2 = """\
#START7777
# YYMMDDHHMI   STN WD1   WS1   WS1 WD2   WS2   WS2     PA    HM    TA    TW    WH    WH    WH    WP  WO AQC MQC
202608221200,22101, 30,  8.2,  9.9, 28,  8.3, 10.2,1013.1, 87.0, 23.5, 25.8,  0.5,  0.3,  0.2,  6.9,241,00000000000000//000,---------------,=
202608221230,22101, 24,  8.8, 10.9, 22,  8.6, 11.1,1012.9, 88.0, 23.2, 25.8,  0.6,-99.0,  0.2,  2.6,  4,00000000000000//000,---------------,=
#7777END
"""
STN = """\
# STN           LON           LAT STN_SP          HT  AD STN_KO               STN_EN               FCT_ID
21229  131.11440000   37.45540000 34000000      0.00 143 울릉도               Ulleungdo            12C20000
22101  126.01880000   37.23610000 24000000      0.00 112 덕적도               Deokjeokdo           12A20000
"""


def test_parse_buoy2_rows():
    df = parse_obs(BUOY2, "buoy")
    r1 = df[(df.station_id == "KMA:22101") & (df.ts == "2026-08-22 03:00:00+00:00")]
    vals = dict(zip(r1["var"], r1["value"]))
    assert vals["hs"] == 0.3 and vals["hmax"] == 0.5 and vals["tp"] == 6.9 and vals["dir"] == 241
    assert vals["wspd"] == 8.2 and vals["slp"] == 1013.1 and vals["sst"] == 25.8
    r2 = df[(df.ts == "2026-08-22 03:30:00+00:00")]
    assert "hs" not in set(r2["var"])                     # -99 결측 제외
    assert "hmax" in set(r2["var"])


def test_parse_station_info():
    st = parse_station_info(STN)
    assert len(st) == 2
    d = st.set_index("station_id").loc["KMA:22101"]
    assert abs(d.lat - 37.2361) < 1e-6 and abs(d.lon - 126.0188) < 1e-6 and d["name"] == "덕적도"
