-- M3's ownership boundary. §5.2 and CLAUDE.md both say the analysis service reads views and
-- cannot write, and that the database enforces it rather than convention. Until this file
-- neither was true: every table sat flat in `public`, the gateway and the analysis service
-- connected as the same superuser, and the claim lived only in prose.
--
-- Three things happen here, in this order, and none of them is optional for the claim:
--
--   1. `ingest` takes every table the gateway writes. Moved, not copied — two copies of a
--      telemetry table is two answers to the same question.
--   2. `read` takes one view per relation the analysis service consumes. The view *is* the
--      contract: the gateway may restructure a table underneath it, and when it cannot, the
--      view change is the reviewable act that says so.
--   3. `analysis` is a login role with USAGE on `read`, SELECT on its views, and nothing
--      else anywhere. "The analysis service must not write" becomes a permission error.
--
-- Three seconds, not none: an ACCESS EXCLUSIVE acquisition (every ALTER ... SET SCHEMA
-- below takes one) that queues behind a long reader blocks every subsequent query on that
-- table, so a migration that waits is an outage while a migration that fails is a retry.
SET lock_timeout = '3s';

CREATE SCHEMA IF NOT EXISTS ingest;
CREATE SCHEMA IF NOT EXISTS read;

-- IF EXISTS on every one of them, because this file is applied on every boot: after the
-- first run the tables are already in `ingest` and there is nothing in `public` to find.
-- Sequences owned by a SERIAL column, indexes and constraints move with their table.
ALTER TABLE IF EXISTS public.raw_events          SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.stations            SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.signals             SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.inspection_results  SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.inspection_images   SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.ingest_gaps         SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.backfill_windows    SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.buffers             SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.carriers            SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.state_changes       SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.buffer_levels       SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.component_lots      SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.components          SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.assemblies          SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.genealogy           SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.part_station_events SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.part_process_values SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.part_process_curves SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.part_dispositions   SET SCHEMA ingest;
ALTER TABLE IF EXISTS public.alarms              SET SCHEMA ingest;
ALTER VIEW  IF EXISTS public.state_changes_settled SET SCHEMA ingest;

-- 001 through 004 name their tables unqualified and are re-applied on every boot, so
-- without this the next boot's `CREATE TABLE IF NOT EXISTS raw_events` would find nothing
-- in `public`, create an empty second copy there, and the gateway would go on writing to
-- the one the search path reaches. That failure is silent in both directions, which is why
-- the search path is set on the *database* — server side, one place, and impossible for a
-- client to forget — rather than in each of the four connection strings that would
-- otherwise have to remember it.
--
-- Deliberately not `read`: the analysis service qualifies every relation it names, so an
-- unqualified table in a query of its own fails loudly instead of resolving to something.
--
-- **READ THIS BEFORE ADDING A SECOND OWNER TO THIS DATABASE.** This is a database-level
-- default and it applies to *every* role that connects, not only the gateway's. §8 of
-- docs/ENGINEERING.md puts the agent's `agent.*` schema in this same instance in M4,
-- migrated by Alembic — and an unqualified `CREATE TABLE` from Alembic would land in
-- `ingest`, which is the gateway's schema and not its own. The symptom is agent tables
-- owned by the wrong role and truncated by the gateway's test fixtures: exactly the silent
-- cross-owner breach the ownership boundary exists to prevent, arriving through the one
-- setting that is not per-owner.
--
-- Whoever creates the `agent` role therefore sets `ALTER ROLE agent SET search_path = agent`
-- in the same migration that creates it. A role-level setting overrides this one, and that
-- is the mechanism — not a convention for Alembic to remember.
DO $set_search_path$
BEGIN
  EXECUTE format(
    'ALTER DATABASE %I SET search_path = ingest, public', current_database());
END
$set_search_path$;

-- LOGIN and no password, so a deployment that never provisions one cannot be reached at
-- all rather than being reachable by whoever guesses first. Whoever owns the database sets
-- it afterwards: Compose through the gateway, the test fixtures for their own container.
-- Never re-created, and the password never reset here — that would silently lock out a
-- running analysis service on the next boot.
DO $create_role$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analysis') THEN
    CREATE ROLE analysis LOGIN;
  END IF;
END
$create_role$;

-- --- the read contract --------------------------------------------------------------------
--
-- Columns are listed rather than `SELECT *`, so that widening a table is not the same act as
-- widening the contract. Three columns §5.2 carries are deliberately absent, and each
-- absence is the database enforcing something the prose already says:
--
--   * `inspection_results.defect_class` is dead. 003's own header says every row written
--     after M2b has it NULL, and `routes_inspection` spent a milestone grouping by it and
--     answering `by_defect_class: []` with no error. It is kept in `ingest` for the M1 rows
--     that still carry one, and not offered to an analysis that must use the vector.
--   * `inspection_results.positions` has no source: §3.4's classifier reports no positions.
--   * `component_lots.depleted_at` is never written, by design — a lot is depleted at the
--     first draw of the next lot on its lane, which is a query over `loaded_at`. Exposed, it
--     would read as "no lot has ever been depleted".
--
-- Nothing withheld here is unavailable; `ingest` still holds all three. What is withheld is
-- the chance to read one of them and believe the answer.

CREATE OR REPLACE VIEW read.stations AS
SELECT id, code, name, function, position_in_line FROM ingest.stations;

CREATE OR REPLACE VIEW read.signals AS
SELECT station_id, signal, source_ts, value FROM ingest.signals;

CREATE OR REPLACE VIEW read.inspection_results AS
SELECT assembly_serial, source_ts, station_id, result, confidence, model_version,
       image_ref, carrier_id, defect_classes, confidences
FROM ingest.inspection_results;

CREATE OR REPLACE VIEW read.inspection_images AS
SELECT assembly_serial, bytes FROM ingest.inspection_images;

CREATE OR REPLACE VIEW read.ingest_gaps AS
SELECT id, from_ts, to_ts, reason FROM ingest.ingest_gaps;

CREATE OR REPLACE VIEW read.buffers AS
SELECT id, code, upstream_station_id, downstream_station_id, capacity FROM ingest.buffers;

CREATE OR REPLACE VIEW read.carriers AS
SELECT id FROM ingest.carriers;

-- Over 002's view rather than over `state_changes`, so the `to_state IS NOT NULL` filter
-- stays stated once. A half-filled row — a StateReason that arrived before the state it
-- belongs to — is not a transition, and a propagation query that counted one would not
-- fail, it would report an episode the plant never had. The unfiltered table is reachable
-- through no view at all, which is what makes forgetting the filter impossible rather than
-- merely discouraged.
CREATE OR REPLACE VIEW read.state_changes_settled AS
SELECT station_id, source_ts, from_state, to_state, reason, reason_buffer_id
FROM ingest.state_changes_settled;

CREATE OR REPLACE VIEW read.buffer_levels AS
SELECT buffer_id, source_ts, level FROM ingest.buffer_levels;

CREATE OR REPLACE VIEW read.component_lots AS
SELECT id, lot_code, lane, supplier, loaded_at FROM ingest.component_lots;

CREATE OR REPLACE VIEW read.components AS
SELECT serial, lot_id, lane, read_at FROM ingest.components;

CREATE OR REPLACE VIEW read.assemblies AS
SELECT serial, created_at, carrier_id FROM ingest.assemblies;

CREATE OR REPLACE VIEW read.genealogy AS
SELECT assembly_serial, component_serial, position FROM ingest.genealogy;

CREATE OR REPLACE VIEW read.part_process_values AS
SELECT assembly_serial, station_id, signal, value FROM ingest.part_process_values;

CREATE OR REPLACE VIEW read.part_process_curves AS
SELECT assembly_serial, station_id, signal, samples FROM ingest.part_process_curves;

CREATE OR REPLACE VIEW read.part_dispositions AS
SELECT assembly_serial, at, disposition, reason FROM ingest.part_dispositions;

CREATE OR REPLACE VIEW read.alarms AS
SELECT id, station_id, code, text, severity, raised_at, acked_at, cleared_at
FROM ingest.alarms;

-- Four of §5.2's relations are reachable through no view, each for its own reason, and
-- `test_read_layer.py`'s `WITHHELD` names all four so that adding one is a deliberate act:
--
--   * `raw_events` and `backfill_windows` are the gateway's own bookkeeping — the verbatim
--     arrival log and R1's reconciliation ledger. An analysis reading them would be
--     answering from what the gateway did rather than from what the plant produced.
--   * `part_station_events` is empty by design: no event §4.1 publishes carries a station
--     entry or exit instant, so every row it could ever return would be an inference.
--   * `state_changes` itself, for the reason stated above `read.state_changes_settled`:
--     unreachable is what makes the `to_state IS NOT NULL` filter impossible to forget
--     rather than merely discouraged.

-- --- the grants ----------------------------------------------------------------------------
--
-- USAGE on `read` and SELECT on what is in it. Nothing on `ingest`, which needs no REVOKE:
-- a new schema grants nothing to anyone, so the absence below is the whole permission.
GRANT USAGE ON SCHEMA read TO analysis;
GRANT SELECT ON ALL TABLES IN SCHEMA read TO analysis;

-- **The trap this milestone was warned about, closed.** ALTER DEFAULT PRIVILEGES applies
-- only to objects created by the role it names, and PostgreSQL does not inherit that
-- through role membership. Name the wrong role and every view a later migration adds
-- silently misses its SELECT grant: nothing fails when the migration runs, nothing fails at
-- deploy, and the first symptom is a reader hitting a view added months earlier.
--
-- So it names whoever is actually connected — `postgres` under Compose, `test` under
-- testcontainers, something else under whatever runs this next — and never a literal.
-- `test_read_layer.py` creates a table and a view here the way a later migration would and
-- reads them as `analysis` with no grant in between, which is the only way to find this
-- wrong before a reader does.
DO $default_privileges$
BEGIN
  EXECUTE format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA read GRANT SELECT ON TABLES TO analysis',
    current_user);
END
$default_privileges$;
