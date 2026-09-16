"""The response shapes. These generate the OpenAPI contract, and §7.3 generates the
frontend's TypeScript from that, so the frontend cannot drift from the API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from analysis import windows
from analysis.patterns import Correction, Dimension
from analysis.propagation import Category, Termination
from analysis.significance import Verdict

AlarmStatus = Literal["raised", "acknowledged", "cleared"]
"""§4.1's alarm lifecycle, as the three shapes the two nullable instants can take.

Derived once, in `alarm_status`, rather than by each reader from `acked_at` and
`cleared_at`: an alarm acknowledged and then cleared is *cleared*, and a reader that
checked `acked_at` first would report the loudest of the three as the quietest.
"""

Aggregation = Literal["raw", "minute", "hour"]
"""§5.3's `agg`. `raw` is every sample; the other two are clock-aligned buckets."""

GroupBy = Literal["time", "carrier", "lane", "defect_class"]
"""§5.3's four groupings for `/inspection/stats`."""

AffectedAnchor = Literal["created", "inspected", "left"]
"""Which per-part instant `/parts/affected` applied its window to.

The three §4.1 publishes against a serial: the assembly's creation at the head of the line,
the inspection verdict, and the disposition at the tail. There is deliberately no fourth for
the press — its per-part record carries two numbers and no time — and see `AppliedCriteria`
for why that is answered with a refusal rather than with the nearest instant available.
"""


def alarm_status(acked_at: datetime | None, cleared_at: datetime | None) -> AlarmStatus:
    """Where an alarm stands, from the two instants §4.1 records against it."""
    if cleared_at is not None:
        return "cleared"
    if acked_at is not None:
        return "acknowledged"
    return "raised"


class Window(BaseModel):
    from_ts: datetime
    to_ts: datetime

    @classmethod
    def of(cls, window: windows.Window) -> Window:
        """The wire shape of the half-open interval `windows.Window` enforces.

        One direction only. Nothing here converts back: the query layer takes the validated
        `windows.Window` the route already built, so a second, unvalidated path from two
        loose timestamps into an interval would be the one place a backwards window could
        reach a query.
        """
        return cls(from_ts=window.from_ts, to_ts=window.to_ts)


class DefectClassCount(BaseModel):
    """One class, and how many parts in the window scored at or above the threshold.

    Not a partition of the rejects: §3.4's six scores are independent and do not sum to 1,
    so a part the model believes carries two defects counts under both. The counts can
    therefore total more than `rejects`, and that is the answer rather than a rounding of it.
    """

    defect_class: str
    count: int


class Gap(BaseModel):
    """§4.4: without gap markers, missing data is indistinguishable from a quiet machine."""

    from_ts: datetime
    to_ts: datetime
    reason: str


class ObservedSpan(BaseModel):
    """What the window actually holds, across every stream the analysis reads.

    This is the half of coverage the gap rows cannot supply. A window with no gaps and no
    rows is a quiet line; a window with no rows because a gap covers all of it is a
    blackout; and the two are the same empty answer until something counts what is there.
    `events` is deliberately a total over the streams rather than a breakdown — the
    question it answers is "is there anything here at all", and a per-stream count would
    invite it being read as a production figure, which it is not.
    """

    from_ts: datetime | None
    to_ts: datetime | None
    events: int


class Coverage(BaseModel):
    """§4.4: without gap markers, missing data is indistinguishable from a quiet machine.

    `fully_covered` is carried rather than left to a reader comparing `covered_fraction`
    against 1.0, for the reason `coverage.Coverage` gives: `gaps == []` and "fully covered"
    are only the same fact when nothing was clipped into `gaps` in the first place.

    `window` is repeated here and in the response that embeds this, and deliberately: the
    coverage of a window is unreadable without the window it is of, and a UI that passes
    this object around alone would otherwise be holding a fraction of nothing.
    """

    window: Window
    gaps: list[Gap]
    covered_fraction: float
    fully_covered: bool
    observed: ObservedSpan


class StatsGroup(BaseModel):
    """One value of the requested grouping, and the reject share within it.

    **`parts` is the denominator and it is not the same quantity for every grouping.** For
    `time`, `carrier` and `lane` the group is a set of parts and `parts` counts them. For
    `defect_class` the group is a *class*, which is not a set of parts at all — §3.4's six
    scores are independent, a part can carry several and most carry none — so the
    denominator is every inspected part in the window, the same number under every class,
    and `rejects` is how many of them scored at or above the threshold on this one.

    That choice is the per-part denominator, and it is the same one `/inspection/patterns`
    tests with: a part is one trial for each class, not one trial shared between them. The
    consequence to read the numbers with is that the defect-class shares do not sum to the
    window's reject rate and were never going to — a part carrying two classes is counted
    under both, exactly as `DefectClassCount` describes.

    `lane` is a third case again, and the one to be careful with: every assembly draws one
    component from *each* feeder lane (§3.5), so the lane groups hold the same parts and
    their `parts` counts sum to more than the window's. The counts are real; what they do
    not support is a comparison between the lanes, which is why `/inspection/patterns`
    refuses that dimension rather than returning a verdict on it.

    `from_ts` and `to_ts` are set for `time` and null for everything else. They are the
    bucket's edges **clipped to the requested window**, so a bucket the window only partly
    covers is visibly short rather than quietly under-counted.
    """

    key: str
    from_ts: datetime | None
    to_ts: datetime | None
    parts: int
    rejects: int
    # Null when `parts` is zero. Not 0.0: a share of "no parts to take a share of" and a
    # share of "parts, none of them rejected" are different facts, and the agent says
    # different things about them.
    reject_share: float | None


class InspectionStats(BaseModel):
    """Counts over a window, with everything needed to tell a real answer from an empty one.

    `by_defect_class` and `rejects_without_class` together account for every reject, and
    neither is readable without the other. They do not sum to `rejects` — a part scoring
    high on two classes appears twice in the breakdown — but `rejects_without_class` is
    exactly the part of `rejects` the breakdown cannot explain, so an empty breakdown beside
    a non-zero `rejects` now says *which* it is: 30 rejects and 30 unclassified is a
    measurement, and 30 rejects and 0 unclassified with an empty breakdown is impossible.
    """

    window: Window
    total: int
    rejects: int
    by_defect_class: list[DefectClassCount]
    # The score `by_defect_class` counted at or above. Reported rather than left in a config
    # file: a count whose meaning depends on a number the reader cannot see is a number the
    # agent would cite as if it meant something else.
    defect_class_threshold: float
    # Rejects in the window that no class reached the threshold for. Two things produce one,
    # and both are real: a row written before §3.4's vector existed, which has no scores at
    # all, and §3.5 scenario 6's decay across every class, where the scores are there and
    # all of them have fallen. Without this number the first is invisible and the second
    # reads as "no defects seen", which is the quietest wrong answer this endpoint can give.
    rejects_without_class: int
    sample_serials: list[str]
    coverage: Coverage
    # Null when the caller asked for no grouping, and an empty list when it asked for one
    # and the window holds nothing to group. Those are different answers and a single
    # empty list would have merged them.
    group_by: GroupBy | None
    groups: list[StatsGroup] | None


class ComponentOrigin(BaseModel):
    """One as-built component of an assembly, and the supplier lot it was drawn from.

    Everything but the serial and the position is nullable, and null means **unknown**, not
    absent: a component named by an assembly whose own read event lies before the gateway's
    history horizon is one this system knows exists and knows nothing else about. Dropping
    such a component would make §3.5 scenario 7's containment list quietly short, which is
    the one failure mode that matters at the moment a containment list is wanted.
    """

    component_serial: str
    position: int
    lane: int | None
    read_at: datetime | None
    lot_code: str | None
    supplier: str | None


class ProcessValue(BaseModel):
    """One number a station recorded against this serial at the instant of production.

    The signal is the name the plant's own event carries (`PeakForce`), which is not
    always the name of the historised stream beside it (`JoiningForcePeak`). Nothing
    between the plant and here translates the two, so nothing can translate them wrongly.
    """

    station: str
    signal: str
    value: float


class ProcessCurve(BaseModel):
    """D6's force–distance curve for this part, as the samples the press recorded.

    The thing the two scalars cannot reconstruct: two presses reach the same peak at the
    same final position by different routes, and the route is the diagnosis (§3.4a).
    """

    station: str
    signal: str
    samples: list[float]


class Inspection(BaseModel):
    """§3.4's verdict for this part, or absent if the part has not been inspected.

    `defect_classes` and `confidences` are parallel arrays over **every** class the
    classifier scores, on good parts too — a good part is six low scores, not an absent
    vector. `confidence` is the confidence in the OK/NOK verdict and is not one of them;
    reading the vector as a distribution is the measured defect that once reported a good
    part as 27 % confident and ~30 % misaligned.

    Both arrays are nullable because M1 wrote rows before the vector existed. Null is
    "this row predates the widened event", not "this part scored nothing".
    """

    source_ts: datetime
    station: str
    result: str
    defect_classes: list[str] | None
    confidences: list[float] | None
    confidence: float | None
    model_version: str
    # Only rejects carry an image (§3.4). None here is a fact, not a missing value.
    image_url: str | None


class Disposition(BaseModel):
    """How the part left the line, and why. Absent while the part is still on it."""

    at: datetime
    disposition: str
    # Null for a good part: the plant sends an empty reason, and an empty reason stored as
    # text makes every good part look like a condition with a nameless cause.
    reason: str | None


class Part(BaseModel):
    """§14's end-to-end trace of one serial, every section read by that serial alone.

    A section that is empty or null is a section this system has no row for, and the part
    is answered anyway. The two shapes that produces are both ordinary rather than
    exceptional: a part between S2 and S3 has no verdict yet, and an assembly created
    before the gateway's history horizon has no creation instant, no carrier and no
    genealogy, because the one event carrying all three arrived before the gateway did.
    """

    assembly_serial: str
    # Null when this gateway never saw the assembly created — a true statement about the
    # part, and the reason the contract does not omit it.
    created_at: datetime | None
    carrier_id: int | None
    genealogy: list[ComponentOrigin]
    process_values: list[ProcessValue]
    process_curves: list[ProcessCurve]
    inspection: Inspection | None
    disposition: Disposition | None


class TimeResolution(BaseModel):
    """What `/time/resolve` made of a phrase, and as of when.

    `now` is echoed because `closed` is a statement about the clock and not about the
    window: the same expression resolved a minute later can answer the same window and a
    different `closed`, and a cache that kept the flag without the instant it was taken at
    would be asserting a fact it cannot support.
    """

    expression: str
    window: Window
    label: str
    closed: bool
    now: datetime


class Stop(BaseModel):
    """§5.4's line stop — an absence of output, not a state.

    `id` is derived from the instant the line stopped producing and resolves against the
    database on its own (§6.5), so two windows that both contain this stop cite it by the
    same id. For a stop already running when the window opened that instant is the part that
    left before it, not the window's edge -- the edge is a property of the question.

    **`id` is null when the database holds no such part**: the window opens before the
    history does, so the stop's start is the caller's own boundary and there is no instant
    to cite that `/stops/{id}` could verify. An id minted anyway would be a citation the
    agent could name and nobody could open.

    `started_before_window` and `open_at_window_end` are what keep `duration_seconds`
    honest: a stop reported as 90 s because the window closed 90 s into it is a different
    claim from a stop that ended after 90 s, and only these two say which was meant.

    `category` is null when the derivation could not be completed, which is an answer
    rather than a gap — see `Derivation`.
    """

    id: str | None
    from_ts: datetime
    to_ts: datetime
    duration_seconds: float
    started_before_window: bool
    open_at_window_end: bool
    category: Category | None


class StopList(BaseModel):
    """Every stop in the window, the micro-stops beneath it, and where the data is not.

    Coverage rides along because a stop list is a claim about *absence* — no part left S4 —
    and absence is exactly what an ingest gap also looks like. A stop list read without it
    would report the gateway's outage as the line's.
    """

    window: Window
    coverage: Coverage
    stops: list[Stop]
    # §5.4: shorter interruptions are counted but are not stop events, because a rising
    # micro-stop count is its own diagnostic signal.
    micro_stops: int
    micro_stop_threshold_seconds: float
    truncated: bool


class StateEpisode(BaseModel):
    """One station holding one state, for a UI to draw as a bar.

    `to_ts` is null while the station was still in this state at the end of the history
    that was read — not "until now", which is a claim about a clock this record has not
    consulted.
    """

    station: str
    state: str
    from_ts: datetime
    to_ts: datetime | None
    reason: str | None
    reason_buffer: str | None


class BufferLevelPoint(BaseModel):
    """One `buffer_levels` row. §4.1 publishes one only when a carrier moves through the
    buffer, so a flat stretch here is a line that moved nothing, not a sampling interval."""

    buffer: str
    at: datetime
    level: int


class DerivationLink(BaseModel):
    """One step of §5.4's chain: an episode, and the buffer that leads to the next.

    `buffer` is null on the last link. `buffer_condition_since` alone being null says the
    episode named a buffer whose empty-or-full moment could not be found within the
    lead-in — the difference between the end of a chain and the end of the evidence.
    """

    station: str
    state: str
    from_ts: datetime
    to_ts: datetime | None
    reason: str | None
    buffer: str | None
    buffer_condition_since: datetime | None


class Unexplained(BaseModel):
    """Where a chain stopped short, named precisely enough to be checked by hand."""

    station: str | None
    buffer: str | None
    detail: str


class Derivation(BaseModel):
    """§5.4's chain as data, seed first and root last.

    Returned whole, with the episode and the buffer behind every step, because §6.5 means
    the agent to be able to *contradict* it — which is only possible against reasoning it
    can see. A verdict with the same category and no links would be the same answer with
    the checking removed.

    `category` is derived from where the chain terminates and is never assigned; null means
    the chain did not terminate, and `unexplained` says where it ran out.

    `cause_candidates` holds every `Held` or `Aborted` episode the walk met. More than one
    is `ambiguous`: the line had two independent faults over the interval, and §5.4 keeps
    both rather than picking the nearer.
    """

    links: list[DerivationLink]
    termination: Termination
    category: Category | None
    cause_candidates: list[StateEpisode]
    unexplained: Unexplained | None


class Alarm(BaseModel):
    """One alarm and its whole lifecycle (§4.1).

    **Not an input to propagation, and this is the file to say so in.** §3.3 and
    `004_m2c.sql` both warn that "the first station to raise an alarm" is circular, and two
    of M2c's eight scenarios raise no alarm at all. An alarm is what a terminated chain is
    annotated with; `/stops/{id}` returns both and connects neither.
    """

    id: int
    station: str
    code: str
    text: str
    severity: int
    raised_at: datetime
    acked_at: datetime | None
    cleared_at: datetime | None
    status: AlarmStatus
    active: bool


class AlarmList(BaseModel):
    """The alarms overlapping a window, newest first.

    Overlap, not containment: an alarm raised before the window and still uncleared inside
    it is one of the more important things the window holds, and a containment test is
    exactly what would drop it.
    """

    window: Window
    # Null when no station filter was applied — distinct from a station that raised nothing.
    station: str | None
    alarms: list[Alarm]


class StopDetail(BaseModel):
    """One stop, everything around it, and §5.4's derivation — shaped for a Gantt.

    `timeline` is every station's episodes over the stop *and the history read before it*,
    so the chart shows the run-up rather than starting at the moment output stopped:
    `history_from_ts` says how far back that goes. `buffer_levels` is the second row of the
    same chart, and is what makes a chain's `buffer_condition_since` checkable by eye.

    `alarms` is an annotation and nothing more. §3.3 and `004_m2c.sql` both warn that "the
    first station to raise an alarm" is circular, and two of M2c's eight scenarios raise no
    alarm at all — so the derivation is computed without them and they are returned beside
    it, never through it.

    `coverage` is over the stop's own interval and is not decoration: both of this stop's
    boundaries were reconstructed from `part_dispositions` rows that are *not there*, and a
    window the gateway was down for holds no such rows either. Without it a five-minute
    outage reads as a five-minute line stop with an unexplained derivation, which is exactly
    the confusion §4.4's gap markers exist to prevent.

    `as_of` is the clock this was answered at, and matters for exactly one case: a stop with
    no part out after it is measured to `as_of`, and `stop.open_at_window_end` is what says
    the end is a reading of the clock rather than an observation of a part.
    """

    stop: Stop
    as_of: datetime
    # Over the stop's own interval. A stop is a claim about absence -- no part left S4 --
    # and an ingest gap is an absence indistinguishable from it here, so a gateway outage
    # would otherwise resolve to a line stop of exactly its length (§4.4).
    coverage: Coverage
    history_from_ts: datetime
    timeline: list[StateEpisode]
    buffer_levels: list[BufferLevelPoint]
    alarms: list[Alarm]
    derivation: Derivation


class TrendPoint(BaseModel):
    """One point of a trend: a bucket, or a single sample when `agg` is `raw`.

    `at` is the bucket's start (half-open, clock-aligned) or the sample's own
    `SourceTimestamp`. `value` is the bucket's mean or the sample's value; `count` is how
    many samples it is of, and is 1 for a raw point.

    `value`, `min_value` and `max_value` are nullable for one reason only: a bucket the
    query emitted with nothing in it. They are never a stand-in for a value that exists.
    """

    at: datetime
    value: float | None
    min_value: float | None
    max_value: float | None
    count: int


class SignalTrend(BaseModel):
    """§5.3's `/signals/trend`, aggregated in SQL.

    `bucket_seconds` is null for `raw` and is the bucket width otherwise. Buckets are
    aligned to the clock in UTC and half-open `[at, at + bucket_seconds)`, so the same
    minute means the same minute across two calls with different windows.

    `truncated` is what stops a cut series reading as a complete one. A trend that stops
    early looks exactly like a signal that stopped, which is a diagnosis.
    """

    station: str
    signal: str
    window: Window
    agg: Aggregation
    bucket_seconds: int | None
    points: list[TrendPoint]
    truncated: bool


class StationStatus(BaseModel):
    """What one station was last seen doing, and when it was last seen.

    `since` is when the state began. `as_of` is the newest row this station has of any
    kind — with the plant down, `since` goes on reading like a live state while `as_of`
    is what says the reading is hours old.
    """

    station: str
    state: str | None
    reason: str | None
    since: datetime | None


class BufferStatus(BaseModel):
    """A buffer's last published level, and when it was published.

    `at` is load-bearing: §4.1 publishes a level only when a carrier moves through, so a
    stopped line's last level is arbitrarily old and the number alone would read as now.

    Both are null for a buffer that has published nothing at all — the line has not moved a
    carrier through it since the gateway connected. The buffer is listed anyway, for the
    reason `StationStatus` lists a silent station: a line reported with two buffers when it
    has three is a wrong picture, where a buffer with an explicit "no level" is a true one.
    """

    buffer: str
    level: int | None
    capacity: int
    at: datetime | None


class LastPartOut(BaseModel):
    """The most recent part to leave S4, which is the line's real output clock."""

    assembly_serial: str
    at: datetime
    disposition: str
    reason: str | None


class LineStatus(BaseModel):
    """§5.3's "what is happening right now", answered honestly with the plant shut down.

    §2.2 requires the diagnostics stack to answer from history when the plant is not
    running, and this is the endpoint where that becomes visible or does not. Every field
    below is the *last known* value and none of them is timestamped "now"; `staleness_seconds`
    is the one number that separates a live line from a stopped gateway, and it is beside
    the threshold it was judged against so that `live` is not a verdict without a reason.

    `staleness_seconds` is null only when the database holds no row at all — a fresh
    deployment, not a stale one, and reporting an infinite staleness there would be a
    measurement of nothing.
    """

    as_of: datetime
    latest_data_at: datetime | None
    staleness_seconds: float | None
    live: bool
    live_within_seconds: float
    stations: list[StationStatus]
    buffers: list[BufferStatus]
    active_alarms: list[Alarm]
    last_part_out: LastPartOut | None


class PatternValue(BaseModel):
    """§5.5's answer for one value of one dimension: everything a reader needs to disagree.

    Observed share, expected share, sample size, effect size and a verdict — and the raw
    p-value beside the corrected one, because the correction is the step a reader most
    needs to check.

    `p_value` and `adjusted_p_value` are null exactly when the verdict is `not_enough_data`.
    A number there would be an invitation to compare it against α somewhere downstream,
    which is the collapse the third verdict exists to prevent.

    `observed_share`, `expected_share` and `effect_size` are null when there was nothing to
    take a share of. Null is "not computed"; it is never a zero.

    `stratum` is the value of the dimension this comparison was made *inside*, and is null
    unless the section was stratified — carrier 7 measured on `misalignment` alone rather
    than on everything it scrapped. Read without it, a class-scoped finding would be taken
    for a line-wide one, which is the opposite of what §3.5 scenario 4 claims.
    """

    value: str
    stratum: str | None
    observed: int
    trials: int
    observed_share: float | None
    expected_share: float | None
    effect_size: float | None
    p_value: float | None
    adjusted_p_value: float | None
    verdict: Verdict


class DimensionPatterns(BaseModel):
    """One dimension's findings, or the reason there can be none.

    `not_comparable` is the field this response exists to be able to set. §5.3 and §5.5
    both list `lane` as a dimension, and §3.5 says in the same document that the line
    cannot distinguish it: every assembly draws one component from each feeder lane, so
    "these parts saw lane 2 and those did not" has no contrast group. Running the test
    anyway would return *not significant* over two groups holding the same parts, and "we
    looked and found nothing" is a materially different — and here false — statement from
    "there is nothing to look at". The counts themselves stay available at
    `/inspection/stats?group_by=lane`, which is where a count with no comparison belongs.

    `unattributed` counts the observations that carry no value along this dimension — a
    part whose carrier the gateway never saw belongs to no carrier and can be compared to
    none. Counted rather than quietly dropped, and **null when `comparable` is false**: no
    observations were built for a dimension nothing was computed over, and a zero there
    would be a measurement nobody made. That is the same distinction `PatternValue` keeps
    between a null share and a zero one, and this endpoint defends it everywhere else.

    `within` is the dimension this section's comparisons were stratified by, and is part of
    the section's identity rather than a note about it: `carrier` and `carrier` within
    `defect_class` are two different questions, both are in this response, and they do not
    have the same answer. §3.5 scenario 4 is the row where the difference decides.
    """

    dimension: Dimension
    within: Dimension | None
    comparable: bool
    not_comparable: str | None
    patterns: list[PatternValue]
    unattributed: int | None


class PatternReport(BaseModel):
    """§5.5 over every dimension, and the empty answer is the expected one.

    `/inspection/patterns` returning nothing significant is what a healthy line looks like,
    and it is the answer that stops the agent inventing a story out of the noise floor.
    `significant_count` is carried so that "nothing" is a number in the response rather
    than an absence a reader has to notice.
    """

    window: Window
    coverage: Coverage
    alpha: float
    minimum_sample: int
    correction: Correction
    # The score at or above which a part is counted as carrying a defect class, repeated
    # from `/inspection/stats` for the same reason it is reported there: the defect-class
    # dimension is a count of parts over this threshold, and the number changes what the
    # count means.
    defect_class_threshold: float
    dimensions: list[DimensionPatterns]
    significant_count: int


class PartGroup(BaseModel):
    """One outcome of a containment scope: how many, and enough serials to act on.

    `count` is exact and `serials` is capped, so a containment list can be trusted as a
    number even where it is too long to be read as a list. `truncated` is what stops the
    capped list being mistaken for the whole of it — the failure mode that matters here is
    a short list read as complete at three in the morning.
    """

    count: int
    serials: list[str]
    truncated: bool


class PartsByOutcome(BaseModel):
    """§5.3's split, and the reason `/parts/affected` exists.

    *"340 serials, 62 rejected, 278 shipped and need checking."* `rejected` is already
    contained; `shipped` is the number someone has to act on tonight; `on_the_line` is
    neither yet, because the part has no disposition row and is still between stations.
    Collapsing the three into a total is what makes a containment answer useless.
    """

    total: int
    rejected: PartGroup
    shipped: PartGroup
    on_the_line: PartGroup


class AppliedCriteria(BaseModel):
    """What `/parts/affected` was actually asked, echoed back — and what the window meant.

    `anchor` is the field a reader must see and `window_selects` is the sentence that says
    what it cost. The window is applied to a *per-part* instant, and which instant that is
    depends on the criteria: with a station it is that station's own record for the part,
    without one it is `assemblies.created_at`. §3.4a forbids reconstructing either by
    joining the time series, so a station whose record carries no instant cannot be asked
    for at all rather than being answered with a neighbouring station's.

    **That matters most exactly where §5.3's own worked example lands.** *"Which parts
    passed S2 while the joining force was out of tolerance?"* is answerable — but not as a
    window on S2, because the press records its two numbers against the serial and no time
    beside them. It is answerable as a *value* question: `signal` with `below` and `above`
    selects the parts whose own recorded force is out of tolerance, which is precisely the
    per-part record §3.4a says exists for this. The window then bounds when those parts were
    *completed*, not when they were pressed, and `window_selects` says so in as many words —
    the two differ by the S2→S3 transit, and pretending they do not would be the same
    approximation §3.4a rejects.
    """

    station: str | None
    carrier: int | None
    lot_code: str | None
    defect_class: str | None
    # The per-part signal the tolerance below applies to, e.g. `PeakForce`. Matched by name
    # across whichever station recorded it: nothing between the plant and here translates
    # signal vocabularies (see `ProcessValue`), so a name belongs to one station in practice.
    signal: str | None
    # A part matches when its recorded value is under `below` **or** over `above` — the
    # out-of-tolerance reading, which is the question a containment scope asks. Either bound
    # alone is the one-sided form.
    below: float | None
    above: float | None
    anchor: AffectedAnchor
    # What the window actually selected, in words. Prose in a contract earns its place here
    # for the same reason it does in `DimensionPatterns.not_comparable`: the distinction it
    # carries is one a reader would otherwise assume away, and an answer nobody can
    # misread is worth a sentence.
    window_selects: str


class AffectedParts(BaseModel):
    """The containment scope: which parts this condition touched, split by where they went.

    `unplaceable` is the count of parts that match every criterion but the window, because
    the instant the window would be applied to is null for them — an assembly created
    before the gateway's history horizon has no creation instant. They are reported rather
    than dropped: a containment list silently short is worse than one that says it is.
    """

    window: Window
    criteria: AppliedCriteria
    parts: PartsByOutcome
    unplaceable: int


class LotRef(BaseModel):
    """One `component_lots` row.

    `depleted_at` is absent and is never coming: 003 never writes it, and a lot is depleted
    at the first draw of the next lot on its lane, which is a query over `loaded_at`.
    Exposed, it would read as "no lot has ever been depleted".
    """

    id: int
    lot_code: str
    lane: int
    supplier: str
    loaded_at: datetime


class LotParts(BaseModel):
    """Which assemblies contain a component from this lot.

    `lots` is a list because a lot code is unique per *lane*, not per line: the same code
    loaded on both feeders is two rows and two populations, and answering as though it were
    one would merge them.
    """

    lot_code: str
    lots: list[LotRef]
    window: Window | None
    parts: PartsByOutcome


class ComponentAssembly(BaseModel):
    """§5.3's single-component recall: a supplier names one serial months later.

    `assembly_serial` null is a real answer and not a miss — the component was read at a
    feeder and has not been built into anything yet. The component being unknown is a 404,
    and the two must not look alike (§6.5).
    """

    component_serial: str
    lane: int | None
    read_at: datetime | None
    lot: LotRef | None
    assembly_serial: str | None
    assembly_created_at: datetime | None
    carrier_id: int | None
    disposition: Disposition | None


class CarrierParts(BaseModel):
    """Which assemblies rode this carrier. §3.1 keeps the carriers in a closed loop, so a
    carrier returns — this is a list over a window, never a list of one pass."""

    carrier_id: int
    window: Window | None
    parts: PartsByOutcome


class KnowledgeAppliesTo(BaseModel):
    """A document's own declaration of where it applies (§6.2's front-matter).

    Served beside the body rather than stripped from it because it is what routing acted
    on: a reader following a `sop` citation can see why that document was loaded, which is
    the difference between a cited procedure and a procedure asserted to exist.
    """

    question_types: list[str] = Field(default_factory=list)
    defect_classes: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    stations: list[str] = Field(default_factory=list)
    alarm_codes: list[str] = Field(default_factory=list)


class KnowledgeDocument(BaseModel):
    """§5.3's `/knowledge/{id}`: one document of the knowledge base, by id.

    `body` is Markdown exactly as the file holds it, front-matter removed. §7.3's `sop`
    citation resolves here, and §7.2's "clicking a citation opens the underlying data"
    is the whole reason this endpoint exists.
    """

    id: str
    title: str
    always_load: bool
    applies_to: KnowledgeAppliesTo
    body: str
