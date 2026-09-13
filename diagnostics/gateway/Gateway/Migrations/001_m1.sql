-- M1 subset of §5.2. Plain PostgreSQL with BRIN indexes on the time columns; TimescaleDB
-- adds operational complexity this data volume does not justify (§5.2).

CREATE TABLE IF NOT EXISTS raw_events (
  id           BIGSERIAL PRIMARY KEY,
  received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  source_ts    TIMESTAMPTZ NOT NULL,
  server_ts    TIMESTAMPTZ NOT NULL,
  kind         TEXT        NOT NULL,
  node_id      TEXT        NOT NULL,
  payload      JSONB       NOT NULL,
  status_code  BIGINT      NOT NULL
);
CREATE INDEX IF NOT EXISTS raw_events_source_ts_brin ON raw_events USING brin (source_ts);

CREATE TABLE IF NOT EXISTS stations (
  id               SMALLSERIAL PRIMARY KEY,
  code             TEXT NOT NULL UNIQUE,   -- discovered by browsing (§4.1)
  name             TEXT NOT NULL,
  function         TEXT,
  position_in_line SMALLINT
);

CREATE TABLE IF NOT EXISTS signals (
  station_id SMALLINT    NOT NULL REFERENCES stations(id),
  signal     TEXT        NOT NULL,
  source_ts  TIMESTAMPTZ NOT NULL,
  value      DOUBLE PRECISION NOT NULL,
  -- §4.4: backfill overlapping live data produces duplicates. So does paging: the
  -- historian's continuation point is the first row of the next page and the next query
  -- re-includes it, so this key fires on every page boundary, not in some edge case.
  PRIMARY KEY (station_id, signal, source_ts)
);
CREATE INDEX IF NOT EXISTS signals_source_ts_brin ON signals USING brin (source_ts);

CREATE TABLE IF NOT EXISTS inspection_results (
  assembly_serial TEXT        PRIMARY KEY,
  source_ts       TIMESTAMPTZ NOT NULL,
  station_id      SMALLINT    NOT NULL REFERENCES stations(id),
  result          TEXT        NOT NULL,      -- good | reject
  defect_class    TEXT,
  confidence      DOUBLE PRECISION,
  model_version   TEXT        NOT NULL,
  image_ref       TEXT
);
CREATE INDEX IF NOT EXISTS inspection_results_source_ts_brin
  ON inspection_results USING brin (source_ts);

CREATE TABLE IF NOT EXISTS inspection_images (
  assembly_serial TEXT PRIMARY KEY REFERENCES inspection_results(assembly_serial),
  bytes           BYTEA NOT NULL     -- rejects only (§3.4)
);

CREATE TABLE IF NOT EXISTS ingest_gaps (
  id      BIGSERIAL   PRIMARY KEY,
  from_ts TIMESTAMPTZ NOT NULL,
  to_ts   TIMESTAMPTZ NOT NULL,
  reason  TEXT        NOT NULL
);

-- R1's reconciliation ledger: what the gateway believes it pulled, per window.
CREATE TABLE IF NOT EXISTS backfill_windows (
  id            BIGSERIAL   PRIMARY KEY,
  from_ts       TIMESTAMPTZ NOT NULL,
  to_ts         TIMESTAMPTZ NOT NULL,
  stream        TEXT        NOT NULL,
  rows_returned INTEGER     NOT NULL,
  rows_written  INTEGER     NOT NULL,
  pages         INTEGER     NOT NULL,
  duration_ms   INTEGER     NOT NULL,
  UNIQUE (from_ts, to_ts, stream)
);
