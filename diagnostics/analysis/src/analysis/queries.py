"""The SQL that feeds the pure modules from `read.*`.

Every function here does one thing: read rows and shape them into the inputs a pure module
takes, or into the response shape a route returns. **No analysis happens in this file.** A
threshold applied here, a duration computed here, a category decided here would be a rule
nothing in `tests/test_stops.py` or `tests/test_propagation.py` can reach — and the point of
those modules being pure is that the rules are all in one place and all testable without a
container.

Relations are named `read.` explicitly, never through a search path. A view the analysis
role has no grant on comes back as `InsufficientPrivilege` from the server, and an
unqualified relation comes back as a loud error rather than resolving to something.

**Where the shapes come from.** Functions feeding a pure module return that module's own
dataclasses (`GapInterval`, `StationEpisode`, `BufferSample`, `LineTopology`, `Observation`).
Functions feeding no pure module — alarms, trends, the line snapshot — return the pydantic
response models directly. A third, parallel set of row dataclasses in between would be one
definition per shape too many, and every one of them would be a place for the wire and the
query to drift apart.

**Nothing here recovers from a failing query.** A read that fails is the database refusing,
which this service has no answer to and must not paper over (CLAUDE.md).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta

from psycopg import Connection, sql

from analysis.coverage import GapInterval
from analysis.models import (
    Alarm,
    BufferLevelPoint,
    BufferStatus,
    LastPartOut,
    ObservedSpan,
    StateEpisode,
    StationStatus,
    TrendPoint,
    alarm_status,
)
from analysis.patterns import Observation
from analysis.propagation import BufferLink, BufferSample, LineTopology, StationEpisode
from analysis.windows import Window

_TIMESTAMPED_STREAMS: tuple[tuple[str, str], ...] = (
    ("read.assemblies", "created_at"),
    ("read.inspection_results", "source_ts"),
    ("read.part_dispositions", "at"),
    ("read.state_changes_settled", "source_ts"),
    ("read.buffer_levels", "source_ts"),
    ("read.signals", "source_ts"),
    ("read.alarms", "raised_at"),
)
"""Every `read.*` relation carrying an instant, and the column that carries it.

Listed once and consumed by both `observed_span` and `latest_data_at`, so "is there data
here" and "how stale is it" are answered over the same set of streams. Two lists would
answer the second from fewer relations than the first, and `/line/status` would then report
a line as stale because the one stream still arriving was not on its list.

Every column is a `SourceTimestamp` (or `assemblies.created_at`, which is one), never a
`ServerTimestamp` and never an arrival order — the invariant all analysis rests on.
`alarms.raised_at` is the plant's own instant for the same reason.
"""

_BUCKET_START = sql.SQL(
    "to_timestamp(floor(extract(epoch from {column}) / {seconds}) * {seconds})"
)
"""Floor an instant to a bucket of `seconds`, aligned to the UTC epoch.

One expression for every bucketed query — `/signals/trend`'s minute and hour, and
`/inspection/stats?group_by=time` — so "the same minute" means the same minute everywhere.
Epoch alignment is clock alignment for any bucket that divides a day, which every bucket
this service offers does, and it is immune to the session's `TimeZone` in a way `date_trunc`
on a `timestamptz` is not.

Half-open: a row at exactly a bucket's start belongs to that bucket and a row at its end to
the next, matching `Window`.
"""


def _bucket_start(column: str, seconds: int) -> sql.Composed:
    return _BUCKET_START.format(
        column=sql.SQL(column),  # a literal column reference, never caller input
        seconds=sql.Literal(seconds),
    )


# --- coverage -------------------------------------------------------------------------


def gaps_overlapping(conn: Connection, window: Window) -> list[GapInterval]:
    """Every `ingest_gaps` row that overlaps `window`.

    Overlap, not containment: a gap that starts before the window and ends inside it still
    makes the window incomplete, and a containment test is what would miss the outage that
    was still running when the window opened.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT from_ts, to_ts, reason FROM read.ingest_gaps "
            "WHERE from_ts < %s AND to_ts > %s ORDER BY from_ts",
            (window.to_ts, window.from_ts),
        )
        return [
            GapInterval(from_ts=row[0], to_ts=row[1], reason=row[2])
            for row in cur.fetchall()
        ]


def observed_span(conn: Connection, window: Window) -> ObservedSpan:
    """The first and last instant the window actually holds, and how many rows that is."""
    query = sql.SQL(
        "SELECT min(at), max(at), count(*) FROM ({streams}) AS observed"
    ).format(
        streams=sql.SQL(" UNION ALL ").join(
            sql.SQL(
                "SELECT {column} AS at FROM {relation} WHERE {column} >= %s AND {column} < %s"
            ).format(column=sql.SQL(column), relation=sql.SQL(relation))
            for relation, column in _TIMESTAMPED_STREAMS
        )
    )
    bounds: list[datetime] = []
    for _ in _TIMESTAMPED_STREAMS:
        bounds.extend((window.from_ts, window.to_ts))

    with conn.cursor() as cur:
        cur.execute(query, bounds)
        row = cur.fetchone() or (None, None, 0)
    return ObservedSpan(from_ts=row[0], to_ts=row[1], events=row[2])


def latest_data_at(conn: Connection) -> datetime | None:
    """The newest instant anywhere in the database, or None when it holds no row at all.

    None is a fresh deployment, not a stale one. `/line/status` reports the two differently
    because "nothing has ever arrived" and "nothing has arrived for six hours" call for
    different actions.
    """
    query = sql.SQL("SELECT max(at) FROM ({streams}) AS newest").format(
        streams=sql.SQL(" UNION ALL ").join(
            sql.SQL("SELECT max({column}) AS at FROM {relation}").format(
                column=sql.SQL(column), relation=sql.SQL(relation)
            )
            for relation, column in _TIMESTAMPED_STREAMS
        )
    )
    with conn.cursor() as cur:
        cur.execute(query)
        row = cur.fetchone()
    return None if row is None else row[0]


# --- stops ----------------------------------------------------------------------------


def output_instants(conn: Connection, window: Window) -> list[datetime]:
    """Every instant a part left S4 inside `window`.

    `part_dispositions` is S4's output and §5.4 defines a stop as its absence. Deliberately
    not a state: a station in `Aborted` is not a stopped line while the buffers below it
    still hold parts, and `stops.py`'s docstring is where that argument lives.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT at FROM read.part_dispositions "
            "WHERE at >= %s AND at < %s ORDER BY at",
            (window.from_ts, window.to_ts),
        )
        return [row[0] for row in cur.fetchall()]


def output_at_or_before(conn: Connection, moment: datetime) -> datetime | None:
    """The last instant a part left S4 at or before `moment`."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max(at) FROM read.part_dispositions WHERE at <= %s", (moment,)
        )
        row = cur.fetchone()
    return None if row is None else row[0]


def output_after(conn: Connection, moment: datetime) -> datetime | None:
    """The first instant a part left S4 strictly after `moment`, or None if none has."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT min(at) FROM read.part_dispositions WHERE at > %s", (moment,)
        )
        row = cur.fetchone()
    return None if row is None else row[0]


# --- propagation ----------------------------------------------------------------------


def topology(conn: Connection) -> LineTopology:
    """The line as `read.buffers` hands it over: one link per buffer.

    Which station is the head and which the tail is discovered from these rows by
    `LineTopology` itself, so a fifth station between two of these needs a buffer row and
    nothing else — here or anywhere.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT b.code, up.code, down.code, b.capacity "
            "FROM read.buffers b "
            "JOIN read.stations up ON up.id = b.upstream_station_id "
            "JOIN read.stations down ON down.id = b.downstream_station_id "
            "ORDER BY b.code"
        )
        return LineTopology(
            links=tuple(
                BufferLink(
                    buffer=row[0],
                    upstream_station=row[1],
                    downstream_station=row[2],
                    capacity=row[3],
                )
                for row in cur.fetchall()
            )
        )


def state_episodes(
    conn: Connection, from_ts: datetime, to_ts: datetime
) -> list[StationEpisode]:
    """Every station's state timeline over `[from_ts, to_ts)`, as episodes.

    **The carry-in row is the whole difficulty.** A station that entered `Execute` an hour
    before the interval and never changed has no row inside it, and a query that read only
    the interval would report that station as having no timeline at all — so the walk would
    find no episode covering its seed instant and terminate `UNEXPLAINED` over a line that
    was running normally. The first half of the union is that station's last transition at
    or before `from_ts`, which is what makes the timeline complete at its left edge.

    An episode's `to_ts` is the next transition of the same station, and None for the last
    one: "still in this state at the end of the history that was read", which is a
    statement about the read and not about a clock.
    """
    with conn.cursor() as cur:
        cur.execute(
            "WITH changes AS ("
            "  (SELECT DISTINCT ON (station_id) station_id, source_ts, to_state, reason,"
            "          reason_buffer_id"
            "     FROM read.state_changes_settled WHERE source_ts < %(from_ts)s"
            "     ORDER BY station_id, source_ts DESC)"
            "  UNION ALL"
            "  (SELECT station_id, source_ts, to_state, reason, reason_buffer_id"
            "     FROM read.state_changes_settled"
            "    WHERE source_ts >= %(from_ts)s AND source_ts < %(to_ts)s)"
            ") "
            "SELECT s.code, c.source_ts, c.to_state, c.reason, b.code "
            "FROM changes c "
            "JOIN read.stations s ON s.id = c.station_id "
            "LEFT JOIN read.buffers b ON b.id = c.reason_buffer_id "
            "ORDER BY s.code, c.source_ts",
            {"from_ts": from_ts, "to_ts": to_ts},
        )
        rows = cur.fetchall()

    return list(_episodes_from(rows))


def _episodes_from(
    rows: Sequence[tuple[str, datetime, str, str | None, str | None]],
) -> Iterator[StationEpisode]:
    """Close each transition against the next one of the same station.

    Assumes `rows` are ordered by station and then by instant, which the query above is
    what guarantees — the pairing is positional and a differently ordered result would
    close an episode against another station's transition.
    """
    for index, (station, source_ts, state, reason, buffer_code) in enumerate(rows):
        following = rows[index + 1] if index + 1 < len(rows) else None
        ends = (
            following[1] if following is not None and following[0] == station else None
        )
        yield StationEpisode(
            station=station,
            state=state,
            from_ts=source_ts,
            to_ts=ends,
            reason=reason,
            reason_buffer=buffer_code,
        )


def buffer_samples(
    conn: Connection, from_ts: datetime, to_ts: datetime
) -> list[BufferSample]:
    """Every buffer level published over `[from_ts, to_ts)`.

    §4.1 publishes a `Level` only when a carrier moves through, so a stopped line publishes
    nothing and the last sample before the silence is the evidence the walk reads. That is
    why the caller reads history well before the stop rather than the stop alone.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT b.code, l.source_ts, l.level "
            "FROM read.buffer_levels l JOIN read.buffers b ON b.id = l.buffer_id "
            "WHERE l.source_ts >= %s AND l.source_ts < %s "
            "ORDER BY b.code, l.source_ts",
            (from_ts, to_ts),
        )
        return [
            BufferSample(buffer=row[0], at=row[1], level=row[2])
            for row in cur.fetchall()
        ]


def buffer_level_points(
    conn: Connection, from_ts: datetime, to_ts: datetime
) -> list[BufferLevelPoint]:
    """The same rows as `buffer_samples`, in the shape a UI draws.

    Two shapes over one query rather than one shape doing both jobs: `BufferSample` is what
    the walk takes and must stay free of the wire, and the wire model must stay free of the
    walk.
    """
    return [
        BufferLevelPoint(buffer=sample.buffer, at=sample.at, level=sample.level)
        for sample in buffer_samples(conn, from_ts, to_ts)
    ]


def episode_models(episodes: Sequence[StationEpisode]) -> list[StateEpisode]:
    """The walk's episodes in the shape a Gantt draws, in station then time order."""
    return [
        StateEpisode(
            station=episode.station,
            state=episode.state,
            from_ts=episode.from_ts,
            to_ts=episode.to_ts,
            reason=episode.reason,
            reason_buffer=episode.reason_buffer,
        )
        for episode in episodes
    ]


# --- alarms ---------------------------------------------------------------------------


_ALARM_COLUMNS = (
    "SELECT a.id, s.code, a.code, a.text, a.severity, a.raised_at, a.acked_at,"
    "       a.cleared_at "
    "FROM read.alarms a JOIN read.stations s ON s.id = a.station_id "
)


def _alarm(
    row: tuple[int, str, str, str, int, datetime, datetime | None, datetime | None],
) -> Alarm:
    return Alarm(
        id=row[0],
        station=row[1],
        code=row[2],
        text=row[3],
        severity=row[4],
        raised_at=row[5],
        acked_at=row[6],
        cleared_at=row[7],
        status=alarm_status(row[6], row[7]),
        active=row[7] is None,
    )


def alarms_overlapping(
    conn: Connection, window: Window, station: str | None
) -> list[Alarm]:
    """Every alarm whose life overlaps `window`, newest first.

    An alarm raised before the window and still uncleared inside it overlaps it: that is
    one of the more consequential things a window can hold, and a containment test on
    `raised_at` alone is exactly what would drop it.
    """
    with conn.cursor() as cur:
        cur.execute(
            _ALARM_COLUMNS + "WHERE a.raised_at < %(to_ts)s "
            "  AND (a.cleared_at IS NULL OR a.cleared_at > %(from_ts)s) "
            "  AND (%(station)s::text IS NULL OR s.code = %(station)s::text) "
            "ORDER BY a.raised_at DESC, a.id DESC",
            {"from_ts": window.from_ts, "to_ts": window.to_ts, "station": station},
        )
        return [_alarm(row) for row in cur.fetchall()]


def alarm(conn: Connection, alarm_id: int) -> Alarm | None:
    """The alarm carrying this id, or None when no row does.

    No window anywhere near it: an id identifies a row, and an alarm that was raised
    outside the interval a reader happens to be looking at is still that alarm.
    """
    with conn.cursor() as cur:
        cur.execute(_ALARM_COLUMNS + "WHERE a.id = %(id)s", {"id": alarm_id})
        row = cur.fetchone()
        return None if row is None else _alarm(row)


def active_alarms(conn: Connection) -> list[Alarm]:
    """Every alarm nobody has cleared, newest first — regardless of how old it is.

    No window, deliberately: an alarm raised before the last restart and never cleared is
    still standing, and bounding this by recency would hide the longest-running fault on
    the line behind the shortest.
    """
    with conn.cursor() as cur:
        cur.execute(
            _ALARM_COLUMNS
            + "WHERE a.cleared_at IS NULL ORDER BY a.raised_at DESC, a.id DESC"
        )
        return [_alarm(row) for row in cur.fetchall()]


# --- signals --------------------------------------------------------------------------


def station_id(conn: Connection, code: str) -> int | None:
    """The station's id, or None when the line has no station with that code.

    Looked up rather than joined in each query so that a caller can tell "no such station"
    from "that station published nothing" — §6.5 rests on 404 and an empty result being
    different answers.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM read.stations WHERE code = %s", (code,))
        row = cur.fetchone()
    return None if row is None else row[0]


def raw_signal_points(
    conn: Connection, station: int, signal: str, window: Window, limit: int
) -> list[TrendPoint]:
    """Every sample of `signal` in the window, oldest first, up to `limit` of them.

    `limit` rows are asked for and the caller is told whether more existed by asking for one
    more than it wants — a series cut short reads exactly like a signal that stopped, which
    is a diagnosis, so the cut has to be visible in the response rather than inferred from
    the row count matching a limit the reader cannot see.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_ts, value FROM read.signals "
            "WHERE station_id = %s AND signal = %s "
            "  AND source_ts >= %s AND source_ts < %s "
            "ORDER BY source_ts LIMIT %s",
            (station, signal, window.from_ts, window.to_ts, limit),
        )
        return [
            TrendPoint(
                at=row[0], value=row[1], min_value=row[1], max_value=row[1], count=1
            )
            for row in cur.fetchall()
        ]


def bucketed_signal_points(
    conn: Connection,
    station: int,
    signal: str,
    window: Window,
    bucket_seconds: int,
    limit: int,
) -> list[TrendPoint]:
    """`signal` aggregated into clock-aligned buckets — in SQL, never in Python.

    §5.3 asks for `minute` and `hour` because an hour of a 6 s takt is six hundred rows a
    signal, and a day is fourteen thousand. Pulling those across the wire to average them
    in Python would move the whole history through this process to produce twenty-four
    numbers; `avg`, `min` and `max` next to the rows is the same answer without the transfer.

    Only buckets that hold at least one sample come back. A bucket with nothing in it is not
    a zero — the line published nothing — and emitting one with a null mean would put a
    point on a chart where there is no measurement.
    """
    query = sql.SQL(
        "SELECT {bucket} AS bucket_from, avg(value), min(value), max(value), count(*) "
        "FROM read.signals "
        "WHERE station_id = %s AND signal = %s "
        "  AND source_ts >= %s AND source_ts < %s "
        "GROUP BY bucket_from ORDER BY bucket_from LIMIT %s"
    ).format(bucket=_bucket_start("source_ts", bucket_seconds))
    with conn.cursor() as cur:
        cur.execute(query, (station, signal, window.from_ts, window.to_ts, limit))
        return [
            TrendPoint(
                at=row[0],
                value=row[1],
                min_value=row[2],
                max_value=row[3],
                count=row[4],
            )
            for row in cur.fetchall()
        ]


# --- the line right now ---------------------------------------------------------------


def latest_station_states(conn: Connection) -> list[StationStatus]:
    """Each station's last settled transition, for every station the line has.

    A LEFT JOIN from `stations`, not an inner one: a station that has published no
    transition at all is part of the line and its absence from this list would read as a
    line with three stations. Its state is null, which says exactly that.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT s.code, c.to_state, c.reason, c.source_ts "
            "FROM read.stations s "
            "LEFT JOIN LATERAL ("
            "  SELECT to_state, reason, source_ts FROM read.state_changes_settled"
            "   WHERE station_id = s.id ORDER BY source_ts DESC LIMIT 1"
            ") c ON true "
            "ORDER BY s.position_in_line, s.code"
        )
        return [
            StationStatus(station=row[0], state=row[1], reason=row[2], since=row[3])
            for row in cur.fetchall()
        ]


def latest_buffer_levels(conn: Connection) -> list[BufferStatus]:
    """Each buffer's last published level, with the instant it was published at.

    The instant is not decoration. §4.1 publishes a level only when a carrier moves through
    the buffer, so a line that has been stopped for an hour reports an hour-old level — and
    a reader that saw the number without the instant would take it for the level now.

    A LEFT JOIN, so a buffer that has published nothing is listed with a null level rather
    than dropped. Dropping it would report a three-buffer line as having two, which is a
    wrong picture of the line rather than a missing number in a right one.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT b.code, l.level, b.capacity, l.source_ts "
            "FROM read.buffers b "
            "LEFT JOIN LATERAL ("
            "  SELECT level, source_ts FROM read.buffer_levels"
            "   WHERE buffer_id = b.id ORDER BY source_ts DESC LIMIT 1"
            ") l ON true "
            "ORDER BY b.code"
        )
        return [
            BufferStatus(buffer=row[0], level=row[1], capacity=row[2], at=row[3])
            for row in cur.fetchall()
        ]


def last_part_out(conn: Connection) -> LastPartOut | None:
    """The most recent part to leave S4. None when none ever has."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT assembly_serial, at, disposition, reason "
            "FROM read.part_dispositions ORDER BY at DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        return None
    return LastPartOut(
        assembly_serial=row[0], at=row[1], disposition=row[2], reason=row[3]
    )


# --- inspection counts and observations -----------------------------------------------


def inspection_totals(conn: Connection, window: Window) -> tuple[int, int]:
    """Parts inspected in the window, and how many of them were rejected."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(*) FILTER (WHERE result = 'reject') "
            "FROM read.inspection_results WHERE source_ts >= %s AND source_ts < %s",
            (window.from_ts, window.to_ts),
        )
        row = cur.fetchone() or (0, 0)
    return row[0], row[1]


def defect_class_counts(
    conn: Connection, window: Window, threshold: float
) -> list[tuple[str, int]]:
    """Each class §3.4's vector scores in this window, and how many parts reached
    `threshold` on it — highest count first, the class name breaking ties.

    **One query behind two exposures**: `/inspection/stats`'s `by_defect_class` and its
    `group_by=defect_class`. Two queries would be two chances to disagree about what a
    defect-class count counts, which is precisely the number this endpoint spent a
    milestone reporting wrongly.

    A class present in the vector and reached by nothing comes back with a count of zero
    rather than being absent: "the classifier scores `crack` and called it on none of these
    600 parts" is a measurement, and an absent row would read as a class that does not exist.

    No filter on `result`, and none is needed: `inspection.classifier` boosts a class only
    where the model believes it saw that defect, and any part with a boosted class is a
    reject. A part that scored high without being rejected would be a thing worth seeing.

    unnest of the two arrays together, so a class stays paired with its own score. They are
    parallel by construction — the plant fills both from one dict and the gateway writes
    both from one event — which is what makes the pairing safe here rather than an
    assumption this query makes about them. A NULL vector unnests to no rows at all, which
    is why a row predating §3.4 contributes to neither the counts nor the classes.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT scored.defect_class, "
            "       count(*) FILTER (WHERE scored.score >= %s) "
            "FROM read.inspection_results r, "
            "     unnest(r.defect_classes, r.confidences) AS scored(defect_class, score) "
            "WHERE r.source_ts >= %s AND r.source_ts < %s "
            "GROUP BY scored.defect_class "
            "ORDER BY count(*) FILTER (WHERE scored.score >= %s) DESC, "
            "         scored.defect_class",
            (threshold, window.from_ts, window.to_ts, threshold),
        )
        return [(row[0], row[1]) for row in cur.fetchall()]


def rejects_without_class(conn: Connection, window: Window, threshold: float) -> int:
    """Rejects in the window that no class reached the threshold for.

    NOT EXISTS over the same unnest `defect_class_counts` groups, so this number and the
    breakdown are the two halves of one rule rather than two rules that can drift. Both
    shapes it counts are real: a row written before §3.4's vector existed carries no scores
    at all, and §3.5 scenario 6 is a run where every score falls together and nothing
    crosses the threshold. Without it, a window where *every* reject looked like that would
    report "no defects seen" while the line scrapped parts.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM read.inspection_results r "
            "WHERE r.source_ts >= %s AND r.source_ts < %s AND r.result = 'reject' "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM unnest(r.defect_classes, r.confidences) AS scored(c, score)"
            "    WHERE scored.score >= %s)",
            (window.from_ts, window.to_ts, threshold),
        )
        row = cur.fetchone() or (0,)
    return int(row[0])


def reject_sample_serials(conn: Connection, window: Window, limit: int) -> list[str]:
    """A handful of reject serials, so an answer can cite parts rather than a bare count."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT assembly_serial FROM read.inspection_results "
            "WHERE source_ts >= %s AND source_ts < %s AND result = 'reject' "
            "ORDER BY source_ts LIMIT %s",
            (window.from_ts, window.to_ts, limit),
        )
        return [row[0] for row in cur.fetchall()]


def counts_by_carrier(conn: Connection, window: Window) -> list[tuple[str, int, int]]:
    """Parts and rejects per carrier, busiest first.

    Carriers the gateway never saw on a part are left out rather than grouped under a null
    key: the reject share of "no carrier recorded" is a property of the history horizon and
    not of the line, and putting it beside the real carriers invites it being read as one.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT carrier_id::text, count(*), "
            "       count(*) FILTER (WHERE result = 'reject') "
            "FROM read.inspection_results "
            "WHERE source_ts >= %s AND source_ts < %s AND carrier_id IS NOT NULL "
            "GROUP BY carrier_id ORDER BY count(*) DESC, carrier_id",
            (window.from_ts, window.to_ts),
        )
        return [(row[0], row[1], row[2]) for row in cur.fetchall()]


def counts_by_lane(conn: Connection, window: Window) -> list[tuple[str, int, int]]:
    """Parts and rejects per feeder lane, by lane number.

    Reached through the genealogy on the serial, never through a time range: which lane a
    component came from is recorded on the component (§3.4a), and reconstructing it from
    when the part was made is exactly the inference this system refuses.

    **These groups are not disjoint.** Every assembly draws one component from each lane, so
    each part appears under both and the counts sum to more than the window's. The counts
    are true; what they cannot support is a comparison, which is why `/inspection/patterns`
    declines the lane dimension rather than testing it (§3.5).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.lane::text, count(DISTINCT r.assembly_serial), "
            "       count(DISTINCT r.assembly_serial) "
            "         FILTER (WHERE r.result = 'reject') "
            "FROM read.inspection_results r "
            "JOIN read.genealogy g ON g.assembly_serial = r.assembly_serial "
            "JOIN read.components c ON c.serial = g.component_serial "
            "WHERE r.source_ts >= %s AND r.source_ts < %s AND c.lane IS NOT NULL "
            "GROUP BY c.lane ORDER BY c.lane",
            (window.from_ts, window.to_ts),
        )
        return [(row[0], row[1], row[2]) for row in cur.fetchall()]


def counts_by_time_bucket(
    conn: Connection, window: Window, bucket_seconds: int
) -> list[tuple[datetime, datetime, datetime, int, int]]:
    """Parts and rejects per clock-aligned bucket: `(bucket_from, clipped_from, clipped_to,
    parts, rejects)`, oldest first.

    **Every bucket the window touches is emitted, including the empty ones.** A bucket with
    no parts is the line having been quiet for an hour, which is a thing a reader needs to
    see; leaving it out would close the gap on the chart and make a stopped hour look like
    an hour that was never asked about.

    The edges come back twice on purpose. `bucket_from` is the bucket's true, clock-aligned
    start — the key, stable across any two windows. The clipped pair is the part of it this
    window actually observed, so a first or last bucket the window only half covers is
    visibly short rather than quietly under-counted against a full hour.
    """
    query = sql.SQL(
        "WITH counted AS ("
        "  SELECT {bucket} AS bucket_from, count(*) AS parts,"
        "         count(*) FILTER (WHERE result = 'reject') AS rejects"
        "    FROM read.inspection_results"
        "   WHERE source_ts >= %(from_ts)s AND source_ts < %(to_ts)s"
        "   GROUP BY bucket_from"
        "), buckets AS ("
        "  SELECT g AS bucket_from"
        "    FROM generate_series({window_bucket}, %(to_ts)s,"
        "                         make_interval(secs => {seconds})) AS g"
        "   WHERE g < %(to_ts)s"
        ") "
        "SELECT b.bucket_from, "
        "       greatest(b.bucket_from, %(from_ts)s), "
        "       least(b.bucket_from + make_interval(secs => {seconds}), %(to_ts)s), "
        "       coalesce(c.parts, 0), coalesce(c.rejects, 0) "
        "FROM buckets b LEFT JOIN counted c USING (bucket_from) "
        "ORDER BY b.bucket_from"
    ).format(
        bucket=_bucket_start("source_ts", bucket_seconds),
        window_bucket=_bucket_start("%(from_ts)s::timestamptz", bucket_seconds),
        seconds=sql.Literal(bucket_seconds),
    )
    with conn.cursor() as cur:
        cur.execute(query, {"from_ts": window.from_ts, "to_ts": window.to_ts})
        return [(row[0], row[1], row[2], row[3], row[4]) for row in cur.fetchall()]


def part_observations(
    conn: Connection, window: Window, bucket_seconds: int
) -> list[Observation]:
    """One trial per inspected part: which carrier it rode, which bucket it fell in, and
    whether it was rejected.

    The denominator is the part, which is what makes the comparison a comparison of like
    with like — carrier 7's reject share against the rest of the pool's, over parts.

    A part whose carrier the gateway never saw carries None and `find_patterns` counts it
    as unattributed rather than dropping it. The lane is deliberately absent: every part
    draws from every lane, so there is no contrast group to compare against (§3.5), and
    filling the field would produce a *not significant* verdict over two groups holding the
    same parts.
    """
    query = sql.SQL(
        "SELECT carrier_id, {bucket}, (result = 'reject') "
        "FROM read.inspection_results "
        "WHERE source_ts >= %s AND source_ts < %s"
    ).format(bucket=_bucket_start("source_ts", bucket_seconds))
    with conn.cursor() as cur:
        cur.execute(query, (window.from_ts, window.to_ts))
        return [
            Observation(
                # The carrier id as text, and not a prettier label: §6.5 verifies every
                # cited id against the database, and `/carriers/{id}/parts` takes this
                # number. A "C08" here would be a finding the agent could name and nobody
                # could open — and it would not match `/inspection/stats?group_by=carrier`,
                # which is the other half of the same answer.
                carrier=None if row[0] is None else str(row[0]),
                time_bucket=row[1].isoformat(),
                outcome=row[2],
            )
            for row in cur.fetchall()
        ]


def defect_class_observations(
    conn: Connection, window: Window, threshold: float
) -> list[Observation]:
    """One trial per part **per class**: did this part reach the threshold on this class,
    and which carrier the part rode.

    This is the per-part denominator applied to a dimension that is not a partition of
    parts. A part is one trial for `gap` and one trial for `crack`, never half a trial for
    each — §3.4's six scores are independent and do not sum to 1, so dividing a part between
    the classes it carries would invent a constraint the classifier does not have.

    The comparison that follows is therefore "is this class called more often than the
    others are, over the same parts", which is the question §5.5 asks of a defect class and
    the one `/inspection/stats?group_by=defect_class` counts the two halves of.

    **The carrier rides along on every row, and one query answers two dimensions.** §3.5
    scenario 4 is a class concentrated on a carrier, which `find_patterns` answers by
    stratifying the carrier comparison within the class — over exactly these trials. A
    second query selecting the same rows with one more column is the copy CLAUDE.md names:
    two SELECTs that agree today and disagree the first time either is retuned.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT scored.defect_class, scored.score >= %s, r.carrier_id "
            "FROM read.inspection_results r, "
            "     unnest(r.defect_classes, r.confidences) AS scored(defect_class, score) "
            "WHERE r.source_ts >= %s AND r.source_ts < %s",
            (threshold, window.from_ts, window.to_ts),
        )
        return [
            Observation(
                defect_class=row[0],
                outcome=row[1],
                # The carrier id as text, for the reason `part_observations` spells out:
                # §6.5 verifies every cited id against the database, and this is the number
                # `/carriers/{id}/parts` and `/parts/affected?carrier=` take.
                carrier=None if row[2] is None else str(row[2]),
            )
            for row in cur.fetchall()
        ]


def lot_observations(conn: Connection, window: Window) -> list[Observation]:
    """One trial per part **per lot it was built from**: was this part rejected.

    The same per-part denominator the defect-class dimension uses, applied to the other
    dimension that is not a partition of parts. An assembly draws one component from each
    feeder lane, so it contains two lots and is one trial for each of them — never half a
    trial for both, which would make the sample size a property of how many lanes the line
    has.

    **Reached through the genealogy on the serial, and that is the whole point.** §3.5's two
    lanes have deliberately staggered lot boundaries, so a lot window belongs to one lane and
    "which lot was current when this part was made" gives a different — wrong — answer from
    "which lot was this part built from". Scenario 7 is a run of rising `gap` defects with a
    *perfectly stable* joining force: the symptom points at a press drift, and the only thing
    that separates that wrong answer from the right one is this correlation. A time join
    would not merely be imprecise here, it would destroy the evidence.

    DISTINCT, because two components of one part drawn from the same lot are one trial for
    that lot and not two.

    LEFT JOIN throughout, so a part whose components carry no lot — one read before the
    gateway's history horizon, or an assembly with no genealogy at all — comes back with a
    null and `find_patterns` counts it as unattributed. An inner join would drop it, and a
    dimension quietly computed over fewer parts than the window holds is the one shape a
    sample gate cannot protect against: it reads as a clean, well-powered answer.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT r.assembly_serial, l.lot_code, (r.result = 'reject') "
            "FROM read.inspection_results r "
            "LEFT JOIN read.genealogy g ON g.assembly_serial = r.assembly_serial "
            "LEFT JOIN read.components c ON c.serial = g.component_serial "
            "LEFT JOIN read.component_lots l ON l.id = c.lot_id "
            "WHERE r.source_ts >= %s AND r.source_ts < %s",
            (window.from_ts, window.to_ts),
        )
        return [Observation(lot=row[1], outcome=row[2]) for row in cur.fetchall()]


def history_bounds(window: Window, history: timedelta) -> tuple[datetime, datetime]:
    """The interval a propagation read must cover for stops inside `window`.

    Stated here rather than at each call site because getting it wrong is silent: the walk
    applies its lead-in again at every link, so a read that covered only the window would
    hand it a timeline that stops mid-chain and the chain would terminate `UNEXPLAINED`
    over evidence the database holds. Nothing in the answer would say so.
    """
    return window.from_ts - history, window.to_ts
