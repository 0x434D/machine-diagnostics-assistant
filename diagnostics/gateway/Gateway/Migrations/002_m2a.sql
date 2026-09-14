-- M2a's slice of §5.2. Plain PostgreSQL with BRIN indexes on the time columns, as
-- 001 established; TimescaleDB adds operational complexity this volume does not
-- justify.

CREATE TABLE IF NOT EXISTS buffers (
  id                    SMALLSERIAL PRIMARY KEY,
  code                  TEXT     NOT NULL UNIQUE,   -- discovered by browsing (§4.1)
  upstream_station_id   SMALLINT NOT NULL REFERENCES stations(id),
  downstream_station_id SMALLINT NOT NULL REFERENCES stations(id),
  capacity              SMALLINT NOT NULL
);

-- Declared, not discovered: §4.1 exposes no carrier nodes. The pool size is a plant
-- configuration value the gateway learns from the carriers it observes, so this table
-- fills as carrier ids are seen rather than at connect. Empty in M2a beyond that --
-- M2b's genealogy is what gives a carrier anything to be joined to.
CREATE TABLE IF NOT EXISTS carriers (
  id SMALLINT PRIMARY KEY
);

-- §3.3: every Suspended transition records which buffer and which direction. The
-- reason is kept verbatim *and* resolved, because "starved:carrier-return" names a
-- real condition with no buffer behind it and must still store.
CREATE TABLE IF NOT EXISTS state_changes (
  station_id       SMALLINT    NOT NULL REFERENCES stations(id),
  source_ts        TIMESTAMPTZ NOT NULL,
  from_state       TEXT,                 -- null on the first state seen after a connect
  -- Nullable, though a settled row always carries one. State and StateReason arrive as
  -- two data changes and a backfill reads history one node at a time, so the reason can
  -- land before the state it belongs to. NOT NULL would make that ordering a failed
  -- batch rather than a row that fills in.
  to_state         TEXT,
  -- Null is the common case, not an error: during bring-up a station publishes six State
  -- transitions while StateReason stays "", and an unchanged value is never sent.
  reason           TEXT,
  reason_buffer_id SMALLINT    REFERENCES buffers(id),
  -- State and StateReason arrive as two data changes sharing one SourceTimestamp.
  -- This key is what makes them one row rather than two.
  PRIMARY KEY (station_id, source_ts)
);
CREATE INDEX IF NOT EXISTS state_changes_source_ts_brin
  ON state_changes USING brin (source_ts);

CREATE TABLE IF NOT EXISTS buffer_levels (
  buffer_id SMALLINT    NOT NULL REFERENCES buffers(id),
  source_ts TIMESTAMPTZ NOT NULL,
  level     SMALLINT    NOT NULL,
  PRIMARY KEY (buffer_id, source_ts)
);
CREATE INDEX IF NOT EXISTS buffer_levels_source_ts_brin
  ON buffer_levels USING brin (source_ts);
