-- M2b's slice of §5.2: the tables identity needs, and D11's widening of
-- inspection_results. Plain PostgreSQL with the BRIN-on-time-columns convention 001 set.
--
-- THE NULLABILITY BELOW IS THE DESIGN, NOT AN OVERSIGHT, and it is the same argument that
-- made 002's state_changes.to_state nullable.
--
-- A part's identity arrives as five event types on four streams, and nothing orders them:
--
--   * the backfill reads one node at a time, so a whole window of S2's press records lands
--     before S1's assembly records for the *next* window;
--   * the gateway's history horizon cuts parts in half. On any boot, the parts in the
--     buffers were created at S1 before the window the backfill starts at and pressed,
--     inspected and sorted inside it — three buffers of five carriers is up to fifteen
--     assemblies whose AssemblyCreatedEvent this gateway will never see;
--   * a drain batch is 200 records and can split a cycle's events across two transactions.
--
-- A NOT NULL foreign key would answer all three with a PostgresException, which QueueDrain
-- catches and retries with nothing acked — an unacknowledgeable record retried forever,
-- ingest stalled, the local queue growing. So the referenced row is created by whichever
-- event names it first, and the columns only its own event can fill stay null until that
-- event arrives, or for ever if it never does. A null created_at is a true statement: this
-- gateway never saw that assembly created.
--
-- The foreign keys themselves are kept. They are what stops a genealogy row pointing at a
-- serial nothing ever mentioned, and with the parent row created on demand they can be
-- satisfied whatever order the streams arrive in.

CREATE TABLE IF NOT EXISTS component_lots (
  id          SMALLSERIAL PRIMARY KEY,
  lot_code    TEXT     NOT NULL,
  lane        SMALLINT NOT NULL,
  supplier    TEXT     NOT NULL,
  -- The instant of the lot's first draw. Every ComponentReadEvent of a lot carries the
  -- same lot code and supplier, so this is the one column an out-of-order read can improve:
  -- the writer lowers it when an earlier draw arrives.
  loaded_at   TIMESTAMPTZ NOT NULL,
  -- Nothing writes this, on purpose. A lot is depleted at the first draw of the next lot on
  -- its lane, which is MIN(loaded_at) over this table for that lane above this row — an
  -- exact query over data already here, not a time-range join. Written instead, it would be
  -- a cached derivation that a backfill reading windows out of order makes momentarily
  -- wrong, and nothing would say which rows were stale.
  depleted_at TIMESTAMPTZ,
  UNIQUE (lot_code, lane)
);

CREATE TABLE IF NOT EXISTS components (
  serial  TEXT PRIMARY KEY,
  -- Null until this component's own ComponentReadEvent arrives. A component named only by
  -- an AssemblyCreatedEvent — one drawn before the history horizon — is a component this
  -- gateway knows exists and knows nothing else about.
  lot_id  SMALLINT REFERENCES component_lots(id),
  lane    SMALLINT,
  read_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS assemblies (
  serial     TEXT PRIMARY KEY,
  -- Null when the assembly was created before this gateway's history horizon, and filled
  -- the moment its AssemblyCreatedEvent arrives whatever order that happens in.
  created_at TIMESTAMPTZ,
  carrier_id SMALLINT REFERENCES carriers(id)
);

-- As-built structure. §3.4a: known exactly at the instant of production.
CREATE TABLE IF NOT EXISTS genealogy (
  assembly_serial  TEXT NOT NULL REFERENCES assemblies(serial),
  component_serial TEXT NOT NULL REFERENCES components(serial),
  position         SMALLINT NOT NULL,
  PRIMARY KEY (assembly_serial, component_serial)
);
CREATE INDEX IF NOT EXISTS genealogy_component ON genealogy (component_serial);

-- §5.2's per-station part history. Nothing in M2b writes it: none of §4.1's five event
-- types carries a station entry or exit instant, and the instant an event was emitted is
-- when the part was *processed*, not when it entered. Declared here because the rest of
-- §5.2's per-part shape is settled in this milestone and a second migration for one table
-- is what D11 argues against; filling it needs an event the plant does not yet publish.
CREATE TABLE IF NOT EXISTS part_station_events (
  assembly_serial TEXT     NOT NULL REFERENCES assemblies(serial),
  station_id      SMALLINT NOT NULL REFERENCES stations(id),
  entered_at      TIMESTAMPTZ NOT NULL,
  left_at         TIMESTAMPTZ,
  state_at_entry  TEXT,
  PRIMARY KEY (assembly_serial, station_id)
);

-- Authoritative per part (§3.4a). The time series still exists for trend questions;
-- this row is what the part itself is asked about.
CREATE TABLE IF NOT EXISTS part_process_values (
  assembly_serial TEXT     NOT NULL REFERENCES assemblies(serial),
  station_id      SMALLINT NOT NULL REFERENCES stations(id),
  signal          TEXT     NOT NULL,
  value           DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (assembly_serial, station_id, signal)
);

-- D6: the curve, not the two scalars, is what separates a press problem from a
-- material problem. An array rather than 40 rows per part.
CREATE TABLE IF NOT EXISTS part_process_curves (
  assembly_serial TEXT     NOT NULL REFERENCES assemblies(serial),
  station_id      SMALLINT NOT NULL REFERENCES stations(id),
  signal          TEXT     NOT NULL,
  samples         DOUBLE PRECISION[] NOT NULL,
  PRIMARY KEY (assembly_serial, station_id, signal)
);

CREATE TABLE IF NOT EXISTS part_dispositions (
  assembly_serial TEXT PRIMARY KEY REFERENCES assemblies(serial),
  at              TIMESTAMPTZ NOT NULL,
  disposition     TEXT NOT NULL,
  -- Null for a good part: the plant sends an empty reason, and an empty reason stored as
  -- text makes every good part look like a condition with a nameless cause.
  reason          TEXT
);

-- D11: inspection_results reaches §5.2's shape in ONE alter, here, rather than one
-- when carriers arrived and another when the vector did.
ALTER TABLE inspection_results ADD COLUMN IF NOT EXISTS carrier_id SMALLINT REFERENCES carriers(id);
ALTER TABLE inspection_results ADD COLUMN IF NOT EXISTS defect_classes TEXT[];
ALTER TABLE inspection_results ADD COLUMN IF NOT EXISTS confidences DOUBLE PRECISION[];
ALTER TABLE inspection_results ADD COLUMN IF NOT EXISTS positions TEXT[];

-- defect_class is M1's and is deliberately left in place and left unfilled. The widened
-- event carries no scalar class, so from here every row's defect_class is NULL. It is kept
-- only for the rows M1 wrote, which still carry a scalar class and are the only history a
-- database upgraded in place holds for the window before this migration ran; dropping the
-- column would delete them. Nothing reads it any more — analysis.routes_inspection grouped
-- by it when this comment was first written, and moved to defect_classes in the same
-- milestone — so the column is droppable whenever pre-M2b history stops being worth keeping.
--
-- positions has no source either: §3.4's classifier reports no defect positions yet. It is
-- here because D11 widens this table once.
