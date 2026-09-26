"""정적 데이터 일괄 다운로드 (전부 무인증 공개 소스, URL은 2026-08-03 검증).

사용:  .venv/bin/python scripts/fetch_sample_data/fetch_static.py [--only gebco|tid|osm|eot20]

대상 → data/static/ :
  gebco  GEBCO_2026 전지구 수심 (netCDF zip, 4.3 GB) — CEDA/BODC, 무인증
  tid    GEBCO_2026 TID 격자 (zip, 99 MB) — 수심 소스 유형(실측/보간) 식별
  osm    OSM water polygons (WGS84 split, ~800 MB) — ODbL
  eot20  EOT20 전지구 조석 조화상수 (SEANOE, CC-BY) — FES2022의 무등록 대체재
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

DATA_STATIC = Path(__file__).resolve().parents[2] / "data" / "static"

SOURCES: dict[str, tuple[str, str]] = {
    "gebco": (
        "https://dap.ceda.ac.uk/bodc/gebco/global/gebco_2026/ice_surface_elevation/netcdf/GEBCO_2026.zip",
        "gebco/GEBCO_2026.zip",
    ),
    "tid": (
        "https://dap.ceda.ac.uk/bodc/gebco/global/gebco_2026/type_identifier_grid/netcdf/gebco_2026_tid.zip",
        "gebco/gebco_2026_tid.zip",
    ),
    "osm": (
        "https://osmdata.openstreetmap.de/download/water-polygons-split-4326.zip",
        "osm/water-polygons-split-4326.zip",
    ),
    "eot20": (
        "https://www.seanoe.org/data/00683/79489/data/85762.zip",
        "eot20/EOT20.zip",
    ),
}


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    done = tmp.stat().st_size if tmp.exists() else 0
    headers = {"Range": f"bytes={done}-"} if done else {}
    mode = "ab" if done else "wb"

    with httpx.stream("GET", url, headers=headers, timeout=120,
                      follow_redirects=True) as r:
        if r.status_code == 416:  # 이미 완료된 .part
            tmp.rename(dest)
            return
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0)) + done
        with open(tmp, mode) as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {dest.name}: {done/1e6:,.0f}/{total/1e6:,.0f} MB "
                          f"({100*done/total:.0f}%)", end="", flush=True)
    print()
    tmp.rename(dest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=list(SOURCES), help="하나만 받기")
    args = parser.parse_args()

    targets = {args.only: SOURCES[args.only]} if args.only else SOURCES
    for name, (url, rel) in targets.items():
        dest = DATA_STATIC / rel
        if dest.exists():
            print(f"[skip] {name}: {dest} already exists")
            continue
        print(f"[get ] {name}: {url}")
        download(url, dest)
        print(f"[ok  ] {name} → {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
