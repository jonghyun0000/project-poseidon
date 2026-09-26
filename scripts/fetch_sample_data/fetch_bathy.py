"""ETOPO 2022 동아시아 서브셋 다운로드 (NCEI THREDDS NetcdfSubset, 무인증).

전지구 파일(GEBCO 7.5 GB) 없이 도메인 수심만 확보한다 (~50 MB).
GEBCO_2026 15초각은 디스크 여유 확보 후 선택 업그레이드 (카탈로그 F1).

사용:  .venv/bin/python scripts/fetch_sample_data/fetch_bathy.py
산출:  data/static/etopo2022_60s_east_asia.nc  (z: 표고 m, 해양은 음수)
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

NCSS = ("https://www.ngdc.noaa.gov/thredds/ncss/grid/global/ETOPO2022/60s/"
        "60s_surface_elev_netcdf/ETOPO_2022_v1_60s_N90W180_surface.nc")
# 주의: 정수 경계값은 NCEI 서버의 부동소수점 검증 버그(52 → 52.000000000000014)를
# 유발하므로 반 셀 안쪽으로 요청한다 (2026-08-04 확인).
PARAMS = {
    "var": "z", "north": 51.99, "south": 11.99, "west": 98.01, "east": 151.99,
    "accept": "netcdf3",
}
DEST = (Path(__file__).resolve().parents[2]
        / "data" / "static" / "etopo2022_60s_east_asia.nc")


def main() -> int:
    if DEST.exists():
        print(f"[skip] {DEST} already exists")
        return 0
    DEST.parent.mkdir(parents=True, exist_ok=True)
    print(f"[get ] ETOPO2022 60s subset {PARAMS['west']}-{PARAMS['east']}E "
          f"{PARAMS['south']}-{PARAMS['north']}N")
    with httpx.stream("GET", NCSS, params=PARAMS, timeout=300,
                      follow_redirects=True) as r:
        r.raise_for_status()
        tmp = DEST.with_suffix(".part")
        with open(tmp, "wb") as f:
            done = 0
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                print(f"\r  {done/1e6:,.0f} MB", end="", flush=True)
    print()
    tmp.rename(DEST)
    print(f"[ok  ] → {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
