"""§5.4's propagation walk: a derivation, not a verdict.

A station in `Suspended` is a consequence **by definition** (§3.3) — it entered that
state because the buffer above it was empty or the one below it was full, and it clears
itself when that changes. So the walk follows the `StateReason` the transition recorded
to the buffer it names, finds when that buffer ran empty or full, finds which station's
episode accounts for that, and repeats. It ends at a cause candidate — a station in
`Held` or `Aborted`, which needs an operator — or at the edge of the line.

**The category is never assigned, only derived**, from where the chain stopped: that is
why `Derivation.category` is a property over the links rather than a field something
sets while walking. And the chain is returned whole, with the episode and the buffer
behind every step, because §5.4 means it to be contradictable: the agent can only
disagree with reasoning it can see.

**Alarms are not an input and must not become one.** §3.3 and `004_m2c.sql`'s header
both say why: "the first station that raised an alarm" is circular — the simulator
writes the alarm and the shutdown from one injected fault, so an analysis reading it
back would be graded on finding what it was handed — and it answers nothing at all for
the two scenarios whose cause lies outside the line, where no alarm is raised anywhere.
An alarm is what a terminated chain is *annotated* with, never how it was found.

Pure: episodes, levels and topology in, a derivation out. No database, no clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Final, Literal

EXECUTE_STATE: Final = "Execute"
"""The state that explains nothing: a station that was running was still filling the
buffer below it and still drawing from the one above."""

CONSEQUENCE_STATE: Final = "Suspended"
CAUSE_CANDIDATE_STATES: Final = frozenset({"Held", "Aborted"})
"""§3.3's table, and the whole of the classification step. Every other PackML state is
neither — a chain that reaches one stops there rather than guessing which it resembles."""

DEFAULT_LEAD_IN: Final = timedelta(minutes=5)
"""How far back of a buffer condition the walk looks for what explains it.

§5.4 asks for the window plus a lead-in because the cause of a stop inside a window
routinely starts before it. Five minutes is ten times the ~30 s a full buffer takes to
drain at §3.1's capacity and takt, so it comfortably spans a chain across all three
buffers — and it is still short enough that an unrelated episode from earlier in the
shift is out of reach. A horizon rather than all of history matters because §4.1
publishes a buffer `Level` only when a carrier moves through it: a stopped line
publishes nothing, and the last sample before the silence is the evidence.
"""

Direction = Literal["starved", "blocked"]
Category = Literal["internal", "external_upstream", "external_downstream", "ambiguous"]


class EpisodeKind(Enum):
    """§3.3's semantics, which are what do the diagnostic work."""

    CONSEQUENCE = "consequence"
    CAUSE_CANDIDATE = "cause_candidate"
    OTHER = "other"


class Termination(Enum):
    """Where the walk stopped. `Derivation.category` is a function of this."""

    CAUSE_CANDIDATE = "cause_candidate"
    LINE_EDGE = "line_edge"
    UNEXPLAINED = "unexplained"


@dataclass(frozen=True)
class StationEpisode:
    """One station in one state, from `from_ts` until `to_ts`.

    `to_ts` is None while the station is still in the state at the end of the timeline
    that was pulled. `reason` is `state_changes.reason` verbatim — §3.3 renders it
    `direction:buffer`, and it carries conditions that are not buffers at all
    (`starved:feeder`, `blocked:outfeed`, `starved:carrier-return`), which is exactly
    why it is kept alongside the resolved `reason_buffer` rather than replaced by it.
    """

    station: str
    state: str
    from_ts: datetime
    to_ts: datetime | None
    reason: str | None = None
    reason_buffer: str | None = None

    @property
    def kind(self) -> EpisodeKind:
        if self.state == CONSEQUENCE_STATE:
            return EpisodeKind.CONSEQUENCE
        if self.state in CAUSE_CANDIDATE_STATES:
            return EpisodeKind.CAUSE_CANDIDATE
        return EpisodeKind.OTHER

    @property
    def direction(self) -> Direction | None:
        """Which way the station was blocked, read off the recorded reason.

        Read from the reason rather than inferred from the topology on purpose: §3.3
        calls this field the thing that makes propagation verifiable rather than
        inferred, and for `starved:feeder` and `blocked:outfeed` there is no buffer to
        infer it from.
        """
        if self.reason is None:
            return None
        recorded = self.reason.split(":", 1)[0]
        if recorded == "starved":
            return "starved"
        if recorded == "blocked":
            return "blocked"
        return None

    def covers(self, moment: datetime) -> bool:
        return self.from_ts <= moment and (self.to_ts is None or moment < self.to_ts)

    def overlaps(self, from_ts: datetime, to_ts: datetime) -> bool:
        return self.from_ts <= to_ts and (self.to_ts is None or self.to_ts > from_ts)


@dataclass(frozen=True)
class BufferSample:
    """One `buffer_levels` row: how many carriers a buffer held at an instant."""

    buffer: str
    at: datetime
    level: int


@dataclass(frozen=True)
class BufferLink:
    """One buffer and the station on each side of it."""

    buffer: str
    upstream_station: str
    downstream_station: str
    capacity: int


@dataclass(frozen=True)
class LineTopology:
    """The line, as `read.buffers` hands it over: one row per buffer.

    Which station is first and which is last is discovered from this rather than
    written down — the head of the line is the station no buffer discharges into — so
    a fifth station between two of these needs a row here and nothing else.
    """

    links: tuple[BufferLink, ...]

    def link(self, buffer: str) -> BufferLink | None:
        for candidate in self.links:
            if candidate.buffer == buffer:
                return candidate
        return None

    def upstream_buffer_of(self, station: str) -> BufferLink | None:
        """The buffer that feeds `station`; None when it is the head of the line."""
        for candidate in self.links:
            if candidate.downstream_station == station:
                return candidate
        return None

    def downstream_buffer_of(self, station: str) -> BufferLink | None:
        """The buffer `station` discharges into; None when it is the tail."""
        for candidate in self.links:
            if candidate.upstream_station == station:
                return candidate
        return None


@dataclass(frozen=True)
class ChainLink:
    """One step of the derivation: an episode, and the buffer that leads to the next.

    `buffer` and `buffer_condition_since` are None on the last link, and
    `buffer_condition_since` alone is None when the episode named a buffer whose
    condition could not be found — the difference between "this is the end of the
    chain" and "this is where the chain ran out of evidence".
    """

    episode: StationEpisode
    buffer: str | None = None
    buffer_condition_since: datetime | None = None

    @property
    def station(self) -> str:
        return self.episode.station


@dataclass(frozen=True)
class Unexplained:
    """Why the walk stopped short, named precisely enough to be checked by hand."""

    station: str | None
    buffer: str | None
    detail: str


@dataclass(frozen=True)
class Derivation:
    """The chain, seed first and root last, plus how it ended.

    `cause_candidates` holds every `Held` or `Aborted` episode the walk met, including
    the one it terminated at. More than one means the line had two independent faults
    over the interval that produced this stop, and §5.4 keeps both rather than picking
    the nearest — which is what `ambiguous` is.
    """

    links: tuple[ChainLink, ...]
    termination: Termination
    cause_candidates: tuple[StationEpisode, ...] = ()
    unexplained: Unexplained | None = None

    @property
    def root(self) -> ChainLink | None:
        """The link the chain terminated at, or None when it did not terminate."""
        if self.termination is Termination.UNEXPLAINED or not self.links:
            return None
        return self.links[-1]

    @property
    def category(self) -> Category | None:
        """§5.4's category, derived from where the chain ends and nowhere else.

        None is an answer: a chain that could not be completed has no category, and
        saying so is the point of returning a derivation rather than a verdict.
        """
        if self.termination is Termination.UNEXPLAINED or not self.links:
            return None
        if len(self.cause_candidates) > 1:
            return "ambiguous"
        if self.termination is Termination.CAUSE_CANDIDATE:
            return "internal"
        # A line edge: the last episode named a condition outside the line, and its
        # direction is what says which end. §5.4 states the rule in exactly these
        # terms -- the chain ends at S1 starved, or at S4 blocked.
        direction = self.links[-1].episode.direction
        if direction == "starved":
            return "external_upstream"
        if direction == "blocked":
            return "external_downstream"
        return None


def derive_chain(
    *,
    station: str,
    at: datetime,
    episodes: Iterable[StationEpisode],
    levels: Iterable[BufferSample],
    topology: LineTopology,
    lead_in: timedelta = DEFAULT_LEAD_IN,
) -> Derivation:
    """Walk §5.4's chain backwards from whatever `station` was doing at `at`.

    `episodes` is every station's state timeline over the window plus the lead-in, and
    `levels` every buffer sample over the same span. Neither needs to be sorted.

    Returns a derivation in every case, including the cases where it could not be
    completed: an unexplained chain says where it stopped and why, because the failure
    mode this module exists to avoid is a plausible link nothing in the data supports.
    """
    timeline = tuple(episodes)
    samples = tuple(levels)

    seed = _episode_covering(timeline, station, at)
    if seed is None:
        return Derivation(
            links=(),
            termination=Termination.UNEXPLAINED,
            unexplained=Unexplained(
                station, None, f"no state episode of {station} covers {at.isoformat()}"
            ),
        )

    links: list[ChainLink] = []
    candidates: list[StationEpisode] = []
    seen: set[tuple[str, datetime]] = set()
    current = seed

    while True:
        step = (current.station, current.from_ts)
        if step in seen:
            # Only reachable from contradictory data -- a starved chain that walks into
            # a blocked one and back -- but it is reachable, and a walk that looped
            # would hang rather than answer.
            return _dead_end(
                links,
                candidates,
                Unexplained(
                    current.station,
                    None,
                    "the chain returns to an episode it already used",
                ),
            )
        seen.add(step)

        kind = current.kind
        if kind is EpisodeKind.CAUSE_CANDIDATE:
            candidates.append(current)
            links.append(ChainLink(current))
            return Derivation(
                links=tuple(links),
                termination=Termination.CAUSE_CANDIDATE,
                cause_candidates=tuple(candidates),
            )
        if kind is EpisodeKind.OTHER:
            links.append(ChainLink(current))
            return _dead_end(
                links,
                candidates,
                Unexplained(
                    current.station,
                    None,
                    f"{current.state} is neither a consequence nor a cause candidate "
                    "under §3.3",
                ),
            )

        direction = current.direction
        buffer_link = (
            topology.link(current.reason_buffer)
            if current.reason_buffer is not None
            else None
        )
        if buffer_link is None or direction is None:
            links.append(ChainLink(current))
            return _outside_the_line(links, candidates, current, direction, topology)

        next_station = (
            buffer_link.upstream_station
            if direction == "starved"
            else buffer_link.downstream_station
        )
        if next_station == current.station:
            links.append(ChainLink(current, buffer_link.buffer))
            return _dead_end(
                links,
                candidates,
                Unexplained(
                    current.station,
                    buffer_link.buffer,
                    f"the recorded direction {direction!r} and the topology disagree "
                    f"about which side of {buffer_link.buffer} this station is on",
                ),
            )

        since = _condition_since(
            samples, buffer_link, direction, current.from_ts, lead_in
        )
        if since is None:
            links.append(ChainLink(current, buffer_link.buffer))
            return _dead_end(
                links,
                candidates,
                Unexplained(
                    next_station,
                    buffer_link.buffer,
                    f"{buffer_link.buffer} is not recorded reaching the condition "
                    f"{current.reason!r} names, within the lead-in",
                ),
            )

        links.append(ChainLink(current, buffer_link.buffer, since))
        explaining, extra = _explaining_episode(timeline, next_station, since, lead_in)
        candidates.extend(extra)
        if explaining is None:
            return _dead_end(
                links,
                candidates,
                Unexplained(
                    next_station,
                    buffer_link.buffer,
                    f"no episode of {next_station} accounts for {buffer_link.buffer} "
                    f"at {since.isoformat()}",
                ),
            )
        current = explaining


def _dead_end(
    links: Sequence[ChainLink],
    candidates: Sequence[StationEpisode],
    unexplained: Unexplained,
) -> Derivation:
    return Derivation(
        links=tuple(links),
        termination=Termination.UNEXPLAINED,
        cause_candidates=tuple(candidates),
        unexplained=unexplained,
    )


def _outside_the_line(
    links: Sequence[ChainLink],
    candidates: Sequence[StationEpisode],
    episode: StationEpisode,
    direction: Direction | None,
    topology: LineTopology,
) -> Derivation:
    """A consequence whose reason names no buffer this topology knows.

    At the head or the tail of the line that is the answer: §3.5's scenarios 1 and 2
    are a dry feeder above S1 and a blocked outfeed below S4, and the reason says which
    (`starved:feeder`, `blocked:outfeed`). In the middle of the line it is not an
    answer, because there is a buffer there and the reason did not name it.

    `starved:carrier-return` at the head is categorised `external_upstream` by the same
    rule, and that is the one place this reads as thinner than §5.4: an empty carrier
    pool is a consequence of something *inside* the line. §5.4 categorises by where the
    chain ends and gives no further rule, and the verbatim reason is on the terminating
    link for a reader to see, so the derivation is not silently wrong — but a caller
    that needs the distinction has to make it from the reason, not from the category.
    """
    at_the_head = (
        direction == "starved" and topology.upstream_buffer_of(episode.station) is None
    )
    at_the_tail = (
        direction == "blocked"
        and topology.downstream_buffer_of(episode.station) is None
    )
    if at_the_head or at_the_tail:
        return Derivation(
            links=tuple(links),
            termination=Termination.LINE_EDGE,
            cause_candidates=tuple(candidates),
        )
    return _dead_end(
        links,
        candidates,
        Unexplained(
            episode.station,
            episode.reason_buffer,
            f"reason {episode.reason!r} names no buffer of this line, and "
            f"{episode.station} is not at the end it would have to be at",
        ),
    )


def _episode_covering(
    timeline: Sequence[StationEpisode], station: str, moment: datetime
) -> StationEpisode | None:
    covering = [
        episode
        for episode in timeline
        if episode.station == station and episode.covers(moment)
    ]
    if not covering:
        return None
    # A station holds one state at a time, so more than one covering episode is
    # contradictory data; the latest is the one the plant reported most recently.
    return max(covering, key=lambda episode: episode.from_ts)


def _condition_since(
    samples: Sequence[BufferSample],
    buffer_link: BufferLink,
    direction: Direction,
    before: datetime,
    lead_in: timedelta,
) -> datetime | None:
    """When the buffer ran empty (`starved`) or full (`blocked`), as of `before`.

    The start of the run of samples that satisfies the condition, not the last one:
    §5.4's worked example reads "B3_4 empty 02:14:32", which is when it became empty
    and stayed so, and that instant is what the next station's episode has to account
    for.
    """
    horizon = before - lead_in
    in_horizon = sorted(
        (
            sample
            for sample in samples
            if sample.buffer == buffer_link.buffer and horizon <= sample.at <= before
        ),
        key=lambda sample: sample.at,
    )
    index = next(
        (
            position
            for position in reversed(range(len(in_horizon)))
            if _satisfies(in_horizon[position], buffer_link, direction)
        ),
        None,
    )
    if index is None:
        return None
    while index > 0 and _satisfies(in_horizon[index - 1], buffer_link, direction):
        index -= 1
    return in_horizon[index].at


def _satisfies(
    sample: BufferSample, buffer_link: BufferLink, direction: Direction
) -> bool:
    if direction == "starved":
        return sample.level <= 0
    return sample.level >= buffer_link.capacity


def _explaining_episode(
    timeline: Sequence[StationEpisode],
    station: str,
    since: datetime,
    lead_in: timedelta,
) -> tuple[StationEpisode | None, tuple[StationEpisode, ...]]:
    """Which episode of `station` accounts for a buffer condition that began at `since`,
    and which other cause candidates of that station overlapped the same interval.

    The episode that covers `since` is the one that accounts for it — a station stops
    first and the buffer below it drains afterwards. Where none covers it, the latest
    one that overlapped the lead-in does: a station that stopped and was restarted can
    empty the buffer below it after it is running again.

    The others are returned separately because they are what makes a derivation
    `ambiguous`: a second fault inside the interval that drained this buffer is a
    second explanation, and §5.4 keeps it rather than dropping it for being further
    away.
    """
    overlapping = [
        episode
        for episode in timeline
        if episode.station == station
        and episode.state != EXECUTE_STATE
        and episode.overlaps(since - lead_in, since)
    ]
    if not overlapping:
        return None, ()
    covering = [episode for episode in overlapping if episode.covers(since)]
    explaining = max(covering or overlapping, key=lambda episode: episode.from_ts)
    extra = tuple(
        sorted(
            (
                episode
                for episode in overlapping
                if episode is not explaining
                and episode.kind is EpisodeKind.CAUSE_CANDIDATE
            ),
            key=lambda episode: episode.from_ts,
        )
    )
    return explaining, extra
