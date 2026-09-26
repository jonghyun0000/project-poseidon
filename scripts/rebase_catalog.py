"""카탈로그 경로 재기준 — 프로젝트 폴더를 옮기거나 복사한 뒤 한 번 실행한다.

왜 필요한가
    data/catalog.sqlite 의 dataset.uri 는 **절대경로**로 저장돼 있다
    (2026-09-11 기준 814건 전부 `/Volumes/T7/클로드 코드 T7/.../data/` 로 시작).
    코드는 이 값을 그대로 연다 — wave_cycle, collocate, api/app.py 등 15곳이
    `xr.open_zarr(rows[-1]["uri"])` 를 쓴다. 반면 새로 쓰는 산출물의 위치는
    config.py 가 프로젝트 기준 상대로 정한다. 그래서 폴더를 옮기면
    **새 산출물은 새 위치에, 기존 814건은 옛 위치를 가리키는** 어긋난 상태가 된다.
    이 스크립트는 그 접두부만 현재 데이터 루트로 바꾼다. 데이터 파일은 건드리지 않는다.

판정 방식
    각 uri 에서 `/data/` 또는 `/data.nosync/` 가 나오는 모든 지점을 후보 분할점으로 보고,
    그 뒤의 상대경로가 **현재 데이터 루트 아래에 실제로 존재하는** 분할점을 고른다.
    존재 확인으로 고르므로 부모 경로에 'data' 가 들어 있어도 오판하지 않는다.

사용
    .venv/bin/python scripts/rebase_catalog.py            # 점검만 (기본, 아무것도 쓰지 않음)
    .venv/bin/python scripts/rebase_catalog.py --apply    # 백업 후 치환
    .venv/bin/python scripts/rebase_catalog.py --db 사본.sqlite --apply   # 다른 DB 대상

    --apply 는 먼저 `catalog.sqlite.bak-<UTC시각>` 백업을 만들고 한 트랜잭션으로 치환한다.
    대상 파일이 없는 행(missing)은 바꾸지 않고 목록만 보고한다.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MARKERS = ("/data/", "/data.nosync/")


def data_root() -> Path:
    """poseidon/core/config.py 와 같은 규칙: POSEIDON_DATA_ROOT, 없으면 <프로젝트>/data."""
    return Path(os.environ.get("POSEIDON_DATA_ROOT", PROJECT_ROOT / "data"))


def split_candidates(uri: str) -> list[str]:
    """uri 안의 데이터 루트 표지 뒤쪽 상대경로 후보를 뒤에서부터 돌려준다."""
    rels = []
    for m in MARKERS:
        start = 0
        while (i := uri.find(m, start)) != -1:
            rels.append(uri[i + len(m):])
            start = i + 1
    return sorted(set(rels), key=len)          # 짧은 것(가장 뒤쪽 분할) 먼저


def plan(db: Path, root: Path) -> tuple[list[tuple[str, str, str]], int, list[tuple[str, str]]]:
    """(바꿀 행, 이미 맞는 행 수, 대상 없음 행) 을 만든다. 쓰지 않는다."""
    con = sqlite3.connect(db)
    rows = con.execute("select dataset_id, uri from dataset").fetchall()
    con.close()
    root_s = str(root).rstrip("/") + "/"
    changes, ok, missing = [], 0, []
    for did, uri in rows:
        uri = str(uri)
        if uri.startswith(root_s) and Path(uri).exists():
            ok += 1
            continue
        target = next((root_s + rel for rel in split_candidates(uri)
                       if rel and (root / rel).exists()), None)
        if target is None:
            missing.append((did, uri))
        elif target != uri:
            changes.append((did, uri, target))
        else:
            ok += 1
    return changes, ok, missing


def main() -> int:
    ap = argparse.ArgumentParser(description="catalog.sqlite 의 dataset.uri 접두부를 현재 데이터 루트로 바꾼다")
    ap.add_argument("--db", type=Path, default=None, help="대상 DB (기본: <데이터 루트>/catalog.sqlite)")
    ap.add_argument("--apply", action="store_true", help="백업 후 실제로 쓴다 (없으면 점검만)")
    a = ap.parse_args()

    root = data_root()
    db = a.db or root / "catalog.sqlite"
    if not db.exists():
        print(f"DB 없음: {db}", file=sys.stderr)
        return 2

    changes, ok, missing = plan(db, root)
    print(f"데이터 루트 : {root}")
    print(f"DB          : {db}")
    print(f"이미 맞음   : {ok}건")
    print(f"치환 대상   : {len(changes)}건")
    print(f"대상 없음   : {len(missing)}건 (바꾸지 않는다)")
    for _, old, new in changes[:3]:
        print(f"  예) {old}\n   -> {new}")
    for did, uri in missing[:10]:
        print(f"  없음) {did}  {uri}")

    if not a.apply:
        print("\n점검만 했다. 쓰려면 --apply" if changes else "\n바꿀 것이 없다.")
        return 0 if not missing else 1
    if not changes:
        print("\n바꿀 것이 없다. 백업도 만들지 않았다.")
        return 0 if not missing else 1

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    bak = db.with_name(f"{db.name}.bak-{stamp}")
    src = sqlite3.connect(db)
    dst = sqlite3.connect(bak)
    src.backup(dst)
    dst.close()
    with src:
        src.executemany("update dataset set uri = ? where dataset_id = ?",
                        [(new, did) for did, _, new in changes])
    src.close()
    print(f"\n백업: {bak}")

    after, ok2, missing2 = plan(db, root)
    print(f"치환 후 — 이미 맞음 {ok2}건, 남은 치환 대상 {len(after)}건, 대상 없음 {len(missing2)}건")
    return 0 if not after and not missing2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
