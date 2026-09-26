"""데이터 카탈로그·계보(lineage) — SQLite 구현.

docs/db/schema.sql(PostGIS)의 논리 스키마 부분집합. 1인 개발 환경에서는 SQLite로 시작하고
배포 단계(Phase 11)에서 PostGIS로 이전한다. 경로 하드코딩 금지 원칙(ADR-003)은 동일하게 적용:
데이터 파일 접근은 반드시 이 카탈로그를 경유한다.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dataset (
  dataset_id TEXT PRIMARY KEY,
  collection TEXT NOT NULL,
  domain     TEXT NOT NULL,
  cycle      TEXT,
  uri        TEXT NOT NULL,
  source_id  TEXT,
  retrieval  TEXT,
  created_at TEXT NOT NULL,
  schema_ver TEXT NOT NULL DEFAULT 'v1'
);
CREATE INDEX IF NOT EXISTS dataset_lookup_idx ON dataset (collection, domain, cycle);

CREATE TABLE IF NOT EXISTS forecast_run (
  run_id   TEXT PRIMARY KEY,
  cycle    TEXT NOT NULL,
  engine   TEXT NOT NULL,
  domain   TEXT NOT NULL,
  status   TEXT NOT NULL,
  degraded INTEGER NOT NULL DEFAULT 0,
  metrics  TEXT,
  UNIQUE (cycle, engine, domain)
);

CREATE TABLE IF NOT EXISTS cycle_event (
  run_id TEXT NOT NULL REFERENCES forecast_run(run_id),
  ts     TEXT NOT NULL,
  event  TEXT NOT NULL,
  detail TEXT
);

CREATE TABLE IF NOT EXISTS error_sample (
  created_at TEXT,
  cycle      TEXT NOT NULL,
  station_id TEXT NOT NULL,
  valid_time TEXT NOT NULL,
  lead_h     REAL NOT NULL,
  var        TEXT NOT NULL,
  predicted  REAL NOT NULL,
  observed   REAL NOT NULL,
  features   TEXT,
  PRIMARY KEY (cycle, station_id, valid_time, var)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Catalog:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── dataset / lineage ─────────────────────────────────────────
    def register_dataset(self, *, collection: str, domain: str, uri: str,
                         cycle: str | None = None, source_id: str | None = None,
                         retrieval: dict[str, Any] | None = None) -> str:
        dataset_id = str(uuid.uuid4())
        with self._conn() as c:
            c.execute(
                "INSERT INTO dataset (dataset_id, collection, domain, cycle, uri, source_id,"
                " retrieval, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (dataset_id, collection, domain, cycle, uri, source_id,
                 json.dumps(retrieval or {}, ensure_ascii=False, default=str), _now()),
            )
        return dataset_id

    def find_datasets(self, collection: str, domain: str,
                      cycle: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM dataset WHERE collection=? AND domain=?"
        args: list[Any] = [collection, domain]
        if cycle is not None:
            q += " AND cycle=?"
            args.append(cycle)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY created_at", args)]

    # ── forecast_run / 상태기계 (ADR-004) ─────────────────────────
    def create_run(self, *, cycle: str, engine: str, domain: str,
                   status: str = "WAITING_SOURCES") -> str:
        run_id = str(uuid.uuid4())
        with self._conn() as c:
            c.execute(
                "INSERT INTO forecast_run (run_id, cycle, engine, domain, status)"
                " VALUES (?,?,?,?,?)",
                (run_id, cycle, engine, domain, status),
            )
        self.add_event(run_id, f"CREATED:{status}")
        return run_id

    def get_run(self, *, cycle: str, engine: str, domain: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute(
                "SELECT * FROM forecast_run WHERE cycle=? AND engine=? AND domain=?",
                (cycle, engine, domain),
            ).fetchone()
        return dict(r) if r else None

    def set_status(self, run_id: str, status: str, *, degraded: bool | None = None) -> None:
        with self._conn() as c:
            if degraded is None:
                c.execute("UPDATE forecast_run SET status=? WHERE run_id=?", (status, run_id))
            else:
                c.execute("UPDATE forecast_run SET status=?, degraded=? WHERE run_id=?",
                          (status, int(degraded), run_id))
        self.add_event(run_id, f"STATUS:{status}")

    # ── 검증·AI 학습 표본 ─────────────────────────────────────────
    def add_error_samples(self, rows: list[dict[str, Any]]) -> int:
        with self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO error_sample "
                "(created_at, cycle, station_id, valid_time, lead_h, var,"
                " predicted, observed, features)"
                " VALUES (:created_at, :cycle, :station_id, :valid_time, :lead_h, :var,"
                " :predicted, :observed, :features)",
                [{**r, "created_at": r.get("created_at") or _now()} for r in rows])
        return len(rows)

    def error_samples(self, var: str = "hs") -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM error_sample WHERE var=? ORDER BY valid_time", (var,))]

    def add_event(self, run_id: str, event: str, detail: dict[str, Any] | None = None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO cycle_event (run_id, ts, event, detail) VALUES (?,?,?,?)",
                (run_id, _now(), event,
                 json.dumps(detail or {}, ensure_ascii=False, default=str)),
            )
