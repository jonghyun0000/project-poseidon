-- Project Poseidon — DB Schema v2 (Phase 2)
-- PostgreSQL 16 + PostGIS 3.4 (+ TimescaleDB 후보: observation, error_sample)
-- 원칙: 격자 데이터는 Zarr(ADR-003), DB는 카탈로그·관측·감사·검증만.

CREATE EXTENSION IF NOT EXISTS postgis;

-- ── 관측 ────────────────────────────────────────────────────────────
CREATE TABLE station (
  station_id   TEXT PRIMARY KEY,              -- 'NDBC:46042', 'KMA:22101', 'KHOA:DT_0001'
  provider     TEXT NOT NULL,                 -- NDBC|KMA|KHOA|CDIP|COOPS|IOC|...
  kind         TEXT NOT NULL,                 -- buoy|wave_buoy|tide_gauge|hf_radar|argo|station
  geom         GEOMETRY(Point, 4326) NOT NULL,
  depth_m      DOUBLE PRECISION,
  meta         JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX station_geom_idx ON station USING GIST (geom);

CREATE TABLE observation (
  station_id   TEXT NOT NULL REFERENCES station,
  ts           TIMESTAMPTZ NOT NULL,
  var          TEXT NOT NULL,                 -- hs|tp|dir|sst|ssh|wl|u10|v10|slp|t|s|spd|drc
  value        DOUBLE PRECISION,
  qc_flag      SMALLINT NOT NULL DEFAULT 0,   -- 0 good / 1 suspect / 2 bad / 9 missing
  qc_detail    JSONB,
  PRIMARY KEY (station_id, ts, var)
);

-- ── 데이터 계보 (lineage) — ADR-003 §3 ─────────────────────────────
CREATE TABLE dataset (
  dataset_id   UUID PRIMARY KEY,
  collection   TEXT NOT NULL,                 -- forcing|boundary|forecast|analysis|obs|static
  domain       TEXT NOT NULL,                 -- global|east-asia|<L2 도메인명>|n/a
  cycle        TIMESTAMPTZ,
  uri          TEXT NOT NULL,                 -- zarr/parquet 경로 (카탈로그 경유 강제)
  source_id    TEXT,                          -- ingest 어댑터 ID
  retrieval    JSONB,                         -- 원본 URL·요청 파라미터·취득시각 (재취득 가능해야 함)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  schema_ver   TEXT NOT NULL DEFAULT 'v1'
);
CREATE INDEX dataset_lookup_idx ON dataset (collection, domain, cycle);

-- ── 예보 사이클 (ADR-004 상태기계) ──────────────────────────────────
CREATE TABLE forecast_run (
  run_id       UUID PRIMARY KEY,
  cycle        TIMESTAMPTZ NOT NULL,
  engine       TEXT NOT NULL,                 -- spectral_wave|shallow_water|blend
  domain       TEXT NOT NULL,
  status       TEXT NOT NULL,                 -- WAITING_SOURCES|...|PUBLISHED|FAILED|ARCHIVED
  degraded     BOOLEAN NOT NULL DEFAULT FALSE,
  corrected    BOOLEAN NOT NULL DEFAULT FALSE,
  dataset_id   UUID REFERENCES dataset,
  metrics      JSONB,                         -- 사이클 자기건전성·사후 스킬 요약
  UNIQUE (cycle, engine, domain)
);

CREATE TABLE cycle_event (                    -- 상태기계 감사 로그
  run_id       UUID NOT NULL REFERENCES forecast_run,
  ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
  event        TEXT NOT NULL,                 -- 상태 전이·재시도·폴백 사유
  detail       JSONB,
  PRIMARY KEY (run_id, ts, event)
);

-- ── 검증·AI ────────────────────────────────────────────────────────
CREATE TABLE error_sample (                   -- 예보-관측 콜로케이션 (AI 학습 원천)
  run_id       UUID NOT NULL REFERENCES forecast_run,
  station_id   TEXT NOT NULL REFERENCES station,
  valid_time   TIMESTAMPTZ NOT NULL,
  lead_s       INTEGER NOT NULL,
  var          TEXT NOT NULL,
  predicted    DOUBLE PRECISION NOT NULL,     -- 물리 원값
  corrected    DOUBLE PRECISION,              -- AI 보정값 (있을 때)
  observed     DOUBLE PRECISION NOT NULL,
  features     JSONB,                         -- 학습 피처 스냅숏 (U10, 수심, fetch, ...)
  PRIMARY KEY (run_id, station_id, valid_time, var)
);

CREATE TABLE model_artifact (                 -- AI 보정 모델 버전 관리
  version      TEXT PRIMARY KEY,              -- 'corr-hs-2026w32'
  kind         TEXT NOT NULL,                 -- correction|surrogate|assimilation
  uri          TEXT NOT NULL,
  trained_on   JSONB NOT NULL,                -- 데이터 범위·제외 부이(leave-buoy-out)
  cv_metrics   JSONB NOT NULL,
  active       BOOLEAN NOT NULL DEFAULT FALSE,-- 검증 통과 시에만 TRUE (동시 활성 1개, 트리거로 강제)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── 경보 ────────────────────────────────────────────────────────────
CREATE TABLE alert (
  alert_id     UUID PRIMARY KEY,
  kind         TEXT NOT NULL,                 -- high_wave|surge|rapid_change|system
  severity     TEXT NOT NULL,                 -- advisory|watch|warning
  geom         GEOMETRY(Geometry, 4326),
  run_id       UUID REFERENCES forecast_run,
  issued       TIMESTAMPTZ NOT NULL,
  expires      TIMESTAMPTZ,
  message      TEXT NOT NULL,
  acknowledged BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX alert_active_idx ON alert (expires) WHERE NOT acknowledged;
