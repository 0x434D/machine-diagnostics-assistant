-- M2c's slice of §5.2: alarms. Plain PostgreSQL with the BRIN-on-time-columns convention
-- 001 set.
--
-- ============================================================================
-- READ THIS BEFORE WRITING A QUERY AGAINST THIS TABLE.
--
-- **The first station that raised an alarm is NOT the root cause, and this table cannot
-- be used to find one.** §3.3 says so in as many words, and it says it about exactly this
-- shape of query:
--
--   * It is **circular**. The simulator produces the alarm and the shutdown from the same
--     injected fault, so `SELECT station_id FROM alarms ORDER BY raised_at LIMIT 1` returns
--     what the plant was told to do. An analysis built on it would be graded on finding
--     something it was handed, and every evaluation number downstream of that is worthless.
--
--   * **It does not apply at all to two of §3.5's eight scenarios.** Scenario 1 is a feeder
--     starved upstream of S1 and scenario 2 is an outfeed blocked after S4; both causes lie
--     outside the line, and NO ALARM IS RAISED ANYWHERE. A query that leans on this table
--     answers those two with an empty result or with whatever unrelated alarm happens to be
--     open, and cannot tell those apart.
--
-- What this table is for is the other half: an alarm is *evidence about a station that shut
-- itself down*, with a code, a text and a severity that §6.4's audit trail and M4's
-- `knowledge/alarms/` documents attach to. §5.4's chain is built from `state_changes` and
-- `buffer_levels` -- it walks a `Suspended` episode back through the buffer its own
-- StateReason names -- and it terminates at a cause candidate. The alarm is what a
-- terminated chain is then *annotated* with, never how it was found.
-- ============================================================================
--
-- **The lifecycle arrives as three events and may arrive in any order.** §4.2 makes an
-- alarm a custom event type with an active state and an acknowledgement state, and the
-- plant fires one event per transition. The history horizon cuts an alarm in half on any
-- boot -- an alarm raised before the window the backfill starts at and acknowledged inside
-- it -- and a truncated window is halved and re-read from its start. So each of the three
-- events carries `AlarmRaisedAt` and any of them creates the row, which is the same rule
-- 003_m2b.sql states for genealogy: the referenced row is written by whichever event names
-- it first, and the columns only one event can fill stay null until that event arrives.
--
-- A null `acked_at` beside a non-null `cleared_at` is therefore a true statement and not a
-- contradiction: this gateway saw the alarm end and never saw it acknowledged.

CREATE TABLE IF NOT EXISTS alarms (
  -- INTEGER, not the SMALLSERIAL every other surrogate key in this schema is. Those key
  -- topology -- four stations, three buffers, ~80 lots -- and are bounded by the plant.
  -- An alarm is an episode: §3.5's row 3 drifts a press that nothing repairs, so it aborts,
  -- an operator restarts it and it aborts again, for as long as the run lasts. SMALLINT's
  -- 32,767 is not a bound anyone has measured this against, and the exhaustion failure is
  -- the one M1 already paid for once.
  id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  station_id  SMALLINT    NOT NULL REFERENCES stations(id),
  code        TEXT        NOT NULL,
  text        TEXT        NOT NULL,
  -- OPC UA's event severity, 1-1000. SMALLINT holds it; a deployment that publishes
  -- something outside that range has published something that is not a severity, and
  -- PostgresWriter refuses it rather than wrapping it into a plausible number.
  severity    SMALLINT    NOT NULL,
  raised_at   TIMESTAMPTZ NOT NULL,
  -- Null is the common case while an alarm is new, and a lasting fact when the
  -- acknowledgement fell outside this gateway's history.
  acked_at    TIMESTAMPTZ,
  -- Null means still active. §3.7's screen lists exactly these rows.
  cleared_at  TIMESTAMPTZ,
  -- The natural key, and what makes the three lifecycle events one row. Not (station,
  -- code) alone: a condition nothing repairs raises again after every restart, and those
  -- are different alarms with different instants rather than one alarm being re-raised.
  UNIQUE (station_id, code, raised_at)
);
CREATE INDEX IF NOT EXISTS alarms_raised_at_brin ON alarms USING brin (raised_at);
