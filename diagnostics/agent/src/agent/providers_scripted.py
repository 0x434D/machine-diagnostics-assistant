"""A provider that does not think, and says so.

This is the default so the whole pipeline — classification, window resolution, the coverage
guard, routing, the tool loop, citation verification against the real database, the answer
object, composition — runs and is tested without credentials. It is the same move §3.4
makes for the vision system, where `SimulatedClassifier` sits behind the interface a real
model would use and "the signature would not change".

Scripted and deterministic on purpose: the same question produces the same classification,
the same tool calls and the same answer every time, so the evaluation harness has a fixture
it can rely on and nobody can mistake the output for reasoning.

**What it demonstrably does not test** is whether a model would choose those tools, read
that question type out of that sentence, or draw those conclusions. It is keyword matching
wearing the interface of a model, and every answer it produces says so.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from agent.classify import CLASSIFY_TOOL
from agent.provider import ProviderReply, ToolCall, results

#: Written into every answer this provider produces, in the prose a reader sees and not
#: only in the trace. A simulated answer that reads like a real one is exactly the quiet
#: wrong answer this system exists not to give.
DISCLOSURE = (
    "This answer was produced by the scripted provider, not by a model. The figures and "
    "citations are real — they come from the database — but the reasoning is fixed."
)

_ACTIONS = (
    "increase",
    "decrease",
    "set ",
    "change",
    "restart",
    "stop the",
    "acknowledge",
)
"""Requests to act on the line. A list of verbs rather than a model call, because this
provider is not a model and pretending otherwise would be the dishonesty this file exists
to avoid."""

_TIME_PHRASES = (
    "last night",
    "last shift",
    "this shift",
    "last 24 hours",
    "last hour",
    "yesterday",
    "today",
    "this week",
    "last week",
)

_SCRAP = ("scrap", "reject", "defect", "quality", "misalign", "crack", "contamin")
_STOP = ("stand", "stood", "stop", "downtime", "standstill", "idle")
_STATUS = ("right now", "currently", "at the moment", "status")
_TREND = ("trend", "developed", "over time", "drift")
_STATISTICS = ("how many", "statistics", "count", "figures")
_TRACE = ("serial", "lot ", "carrier", "which parts", "contain")
_KNOWLEDGE = ("what does", "what is", "explain", "mean")
_AMBIGUOUS = ("losing parts", "lose parts", "losing output")

_ROUTED = re.compile(r"^## ([A-Za-z0-9._-]+) — ", re.MULTILINE)
"""The document headings `pipeline._system` writes. This provider reads its own context the
way a model reads its own context; nothing hands it the routing decision privately."""

_INTERVENTION_STATES = ("held", "aborted")
"""DP-11's shapes: a chain terminating at a station a *person* put into that state. `Held`
is a cause candidate precisely because it requires an operator, which is the same property
that makes "held because it faulted" and "held because somebody stopped it" identical to
the computation."""

_SERIAL = re.compile(r"\b([A-Z]-\d{8})\b")
_STATION = re.compile(r"\bS(\d)\b", re.IGNORECASE)
_ALARM = re.compile(r"\bA-\d{3,}\b", re.IGNORECASE)


class ScriptedProvider:
    """Needs no credentials and reaches no network."""

    name = "scripted"

    async def call(
        self,
        system: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ProviderReply:
        question = _question(messages)
        names = [str(tool.get("name")) for tool in tools]

        if names == [CLASSIFY_TOOL["name"]]:
            return ProviderReply(
                tool_calls=[
                    ToolCall(
                        id="call_classify", name="classify", arguments=_read(question)
                    )
                ]
            )

        # §6.5 forbids citing a procedure that was never read, so the only procedures this
        # provider can cite are the ones it can see — which are the ones in the system
        # prompt routing built. Read from there rather than from a list here, so a document
        # routing dropped under budget cannot be cited either.
        return _investigate(question, results(messages), _documents(system))


def _documents(system: str) -> frozenset[str]:
    return frozenset(_ROUTED.findall(system))


def _question(messages: Sequence[Mapping[str, object]]) -> str:
    content = messages[0].get("content") if messages else ""
    return str(content).lower() if isinstance(content, str) else ""


def _read(question: str) -> dict[str, object]:
    """The classification, from keywords. Returns no `question_type` when nothing matches,
    which is what §6.1's "unclassifiable" fallback is for — inventing one here would be the
    guess the fallback exists to refuse."""
    arguments: dict[str, object] = {
        "time_phrase": next((p for p in _TIME_PHRASES if p in question), ""),
        "singular": any(
            word in question for word in ("why did", "why was", "the stop")
        ),
    }

    stations = [f"S{digit}" for digit in _STATION.findall(question)]
    if stations:
        arguments["stations"] = stations
    codes = [code.upper() for code in _ALARM.findall(question)]
    if codes:
        arguments["alarm_codes"] = codes

    if any(verb in question for verb in _ACTIONS):
        arguments["question_type"] = "out_of_scope"
        return arguments

    scrap = any(word in question for word in _SCRAP)
    if any(word in question for word in _AMBIGUOUS):
        # §6.7's fourth row: downtime or scrap, and the answers differ. The disambiguator
        # is whether the question names one of them; without it neither reading is more
        # likely and the pipeline asks.
        readings = [
            {
                "question_type": "quality_investigation",
                "description": "parts are being scrapped",
            },
            {
                "question_type": "stop_investigation",
                "description": "the line is standing still",
            },
        ]
        if not scrap:
            readings.reverse()
        arguments["readings"] = readings
        arguments["ranked"] = scrap
        arguments["question_type"] = (
            "quality_investigation" if scrap else "stop_investigation"
        )
        return arguments

    for words, question_type in (
        (_STOP, "stop_investigation"),
        (_STATUS, "status"),
        (_TREND, "trend"),
        (_KNOWLEDGE, "knowledge"),
        (_TRACE, "traceability"),
    ):
        if any(word in question for word in words):
            if question_type == "knowledge" and scrap and "mean" not in question:
                break
            arguments["question_type"] = question_type
            return arguments

    if scrap and any(word in question for word in _STATISTICS):
        arguments["question_type"] = "statistics"
        return arguments
    if scrap:
        arguments["question_type"] = "quality_investigation"
        return arguments
    if any(word in question for word in _STATISTICS):
        arguments["question_type"] = "statistics"
        return arguments

    return arguments


def _investigate(
    question: str,
    seen: Mapping[str, dict[str, object]],
    documents: frozenset[str] = frozenset(),
) -> ProviderReply:
    """One turn of the tool loop, decided by what is already in the transcript.

    Reading its own transcript is how this provider stands in for a model without being
    handed a private channel the real one would not have: everything it decides on, a model
    would have seen too.
    """
    arguments = _read(question)
    question_type = str(arguments.get("question_type", "knowledge"))

    if question_type == "stop_investigation":
        stops = _answered(seen, "list_stops")
        if stops is None:
            return _ask("list_stops", {})
        chosen = _chosen(stops)
        detail = _answered(seen, "get_stop")
        if detail is None and chosen is not None:
            return _ask("get_stop", {"identifier": str(chosen.get("id"))})
        return ProviderReply(final=_stop_final(detail, documents))

    if question_type == "status":
        status = _answered(seen, "line_status")
        if status is None:
            return _ask("line_status", {})
        return ProviderReply(final=_status_final(status))

    if question_type == "trend":
        trend = _answered(seen, "signal_trend")
        if trend is None:
            stations = arguments.get("stations")
            station = (
                str(stations[0]) if isinstance(stations, list) and stations else "S2"
            )
            return _ask("signal_trend", {"station": station, "signal": "JoiningForce"})
        return ProviderReply(final=_trend_final(trend))

    if question_type == "traceability":
        match = _SERIAL.search(question.upper())
        part = _answered(seen, "get_part")
        if part is None and match is not None:
            return _ask("get_part", {"serial": match.group(1)})
        return ProviderReply(final=_part_final(part))

    if question_type in ("statistics", "quality_investigation"):
        stats = _answered(seen, "inspection_stats")
        if stats is None:
            return _ask("inspection_stats", {})
        return ProviderReply(final=_stats_final(stats))

    return ProviderReply(final={"findings": []})


def _answered(
    seen: Mapping[str, dict[str, object]], name: str
) -> Mapping[str, object] | None:
    """The result of a call that succeeded.

    A tool that came back an error has not answered, so this provider asks again — which is
    what a model would do, and what makes §6.8's consecutive-failure limit the thing that
    stops a dead service rather than a first refusal being mistaken for an answer.
    """
    result = seen.get(name)
    if result is None or result.get("error"):
        return None
    return result


def _ask(name: str, arguments: Mapping[str, object]) -> ProviderReply:
    return ProviderReply(
        tool_calls=[ToolCall(id=f"call_{name}", name=name, arguments=arguments)]
    )


def _chosen(stops: Mapping[str, object]) -> Mapping[str, object] | None:
    entries = stops.get("stops")
    if not isinstance(entries, list) or not entries:
        return None
    assumed = stops.get("assumed_stop")
    if isinstance(assumed, dict):
        return assumed
    first = entries[0]
    return first if isinstance(first, dict) else None


# --- the findings this provider is able to state ---------------------------------------
#
# Each one is a sentence about numbers that came out of the database, with the semantics
# they were counted under travelling beside them. Nothing here interprets: an interpretation
# is a `hypothesis`, and §6.3 requires evidence strength behind one.


def _finding(
    statement: str,
    basis: str,
    citations: list[dict[str, object]],
    evidence_strength: str | None = None,
) -> dict[str, object]:
    finding: dict[str, object] = {
        "statement": statement,
        "basis": basis,
        "citations": citations,
    }
    if evidence_strength is not None:
        finding["evidence_strength"] = evidence_strength
    return finding


def _stats_final(stats: Mapping[str, object]) -> dict[str, object]:
    total = _int(stats.get("total"))
    rejects = _int(stats.get("rejects"))
    if total == 0:
        # Not the same claim as zero rejects, and the difference is the whole point of
        # §1's proof: nothing was recorded, so there is nothing to count.
        return {
            "findings": [],
            "caveats": [
                "No parts were recorded in that window — no data, not zero rejects."
            ],
        }

    window = stats.get("window")
    span = _span(window if isinstance(window, dict) else {})
    samples = stats.get("sample_serials")
    serials = [str(s) for s in samples][:3] if isinstance(samples, list) else []

    findings = [
        _finding(
            f"{total} parts were inspected{span}, of which {rejects} were rejected.",
            "measured",
            [{"kind": "part", "id": serial} for serial in serials],
        )
    ]

    # §3.4's six scores are independent and do not sum to 1, so the breakdown is not a
    # partition of the rejects: a part the model believes carries two defects is counted
    # under both, and a reject no class reached the threshold for is counted under none.
    # Stating the counts alone would state a number under a semantics the reader is never
    # given, so the threshold and the remainder travel with them. Not defaulted the way the
    # counts above are: a count has a true zero and a threshold does not, so a default here
    # would put a threshold this service never counted at into a `measured` sentence.
    # `bool` is a subclass of `int`, so an unguarded isinstance would let `True` through and
    # render it as the threshold the counts were taken at.
    raw = stats.get("defect_class_threshold")
    threshold = (
        raw if isinstance(raw, int | float) and not isinstance(raw, bool) else None
    )
    classes = stats.get("by_defect_class")
    unaccounted = _int(stats.get("rejects_without_class"))
    caveats: list[str] = []

    if threshold is None and (classes or unaccounted):
        # Absent, the two findings below have no statable semantics at all, so they are
        # withheld and the withholding is said out loud.
        caveats.append(
            "The analysis service did not say which score threshold its defect breakdown "
            "counted at, so the breakdown is not reported."
        )
    if threshold is not None and isinstance(classes, list) and classes:
        breakdown = ", ".join(
            f"{entry['defect_class']} {entry['count']}"
            for entry in classes
            if isinstance(entry, dict)
        )
        findings.append(
            _finding(
                f"By defect class, counting every class scoring {threshold} or above — so "
                f"a part with two defects is counted twice: {breakdown}.",
                "measured",
                [],
            )
        )
    if threshold is not None and unaccounted:
        findings.append(
            _finding(
                f"{unaccounted} of those {rejects} rejects had no class scoring "
                f"{threshold} or above, so the breakdown does not explain them.",
                "measured",
                [],
            )
        )
    return {"findings": findings, "caveats": caveats}


def _stop_final(
    detail: Mapping[str, object] | None, documents: frozenset[str] = frozenset()
) -> dict[str, object]:
    if detail is None:
        return {
            "findings": [],
            "caveats": ["No stop in that window could be opened."],
        }
    stop = detail.get("stop")
    if not isinstance(stop, dict):
        return {"findings": [], "caveats": ["The stop record carried no stop."]}

    seconds = _float(stop.get("duration_seconds"))
    category = stop.get("category")
    cite = _cite_stop(stop, documents, "SOP-01")
    findings = [
        _finding(
            f"The line stood for {seconds:.0f} s from {_clock(stop.get('from_ts'))} to "
            f"{_clock(stop.get('to_ts'))} UTC.",
            "measured",
            cite,
        )
    ]

    derivation = detail.get("derivation")
    root = _root(derivation)
    if root is not None:
        reason = root.get("reason")
        findings.append(
            _finding(
                f"The propagation chain terminates at {root.get('station')} in state "
                f"{root.get('state')}"
                + (f" ({reason})" if reason else "")
                + (f", which categorises the stop as {category}." if category else "."),
                # §5.4's chain is bookkeeping over a graph and a timeline: it follows
                # necessarily from the state history rather than being read off it.
                "derived",
                cite,
            )
        )
    if isinstance(derivation, dict) and derivation.get("unexplained"):
        findings.append(
            _finding(
                "One link in the chain is unexplained, so the root is where the "
                "evidence stops rather than where the cause is.",
                "derived",
                cite,
            )
        )

    final: dict[str, object] = {"findings": findings, "caveats": []}
    _contradict(final, detail, stop, root, documents)
    return final


def _root(derivation: object) -> Mapping[str, object] | None:
    if not isinstance(derivation, dict):
        return None
    links = derivation.get("links")
    if not isinstance(links, list) or not links:
        return None
    first = links[0]
    return first if isinstance(first, dict) else None


def _contradict(
    final: dict[str, object],
    detail: Mapping[str, object],
    stop: Mapping[str, object],
    root: Mapping[str, object] | None,
    documents: frozenset[str],
) -> None:
    """DP-11, applied to the derivation §5.4 returned as data.

    Possible only because the chain arrives as links rather than as a verdict — the
    ordering *is* the argument, and an answer that only said "root: S3" could be disagreed
    with but not argued against.

    Two of DP-11's five checks are computable from this response and both must hold: the
    terminating station is in a state a person puts it in and carries **no alarm of its
    own**, and something else on the line was already disturbed **before** it. A cause
    precedes its consequences; an intervention that starts second is a response to the
    first thing, not the origin of it. The other three checks — acknowledgement timing,
    restart-then-stop-again, whether the intervention explains the recovery — need more
    than one stop's record and are left to a reader, which the reasoning says.
    """
    if root is None or str(root.get("state", "")).lower() not in _INTERVENTION_STATES:
        return
    station = str(root.get("station"))
    if _has_alarm(detail, station):
        # DP-11's own refutation: a station carrying its own alarm is a genuine cause
        # candidate and the computation is right.
        return
    earlier = _earlier_disturbance(detail, station, str(root.get("from_ts")))
    if earlier is None:
        return

    other = str(earlier.get("station"))
    ordering = (
        f"{other} entered {earlier.get('state')} at {_clock(earlier.get('from_ts'))} "
        f"UTC, {station} was {root.get('state')} at {_clock(root.get('from_ts'))} UTC — "
        f"{station} second, and with no alarm of its own in this stop."
    )
    final["contradiction"] = {
        "derived_root": station,
        "agent_root": other,
        "reasoning": (
            f"The chain terminates at {station}, but {station} was put into "
            f"{root.get('state')} after {other} was already disturbed, and raised no alarm "
            f"of its own. {ordering} A cause precedes its consequences, so this reads as an "
            f"operator intervention the chain followed rather than the fault it started "
            f"from. Acknowledgement timing and whether the line recovered when a different "
            f"station was cleared would settle it, and are not in this record."
        ),
    }
    findings = final["findings"]
    if isinstance(findings, list):
        findings.append(
            _finding(
                f"The computed root is {station}; on the ordering the root is {other}, and "
                f"{station} is where a person stopped the line rather than where it broke.",
                # §6.3: it required interpretation from the knowledge base, and DP-11 says
                # so itself — "The contradiction is a `hypothesis`, and its evidence
                # strength is that sequence."
                "hypothesis",
                _cite_stop(stop, documents, "DP-11"),
                evidence_strength=ordering,
            )
        )


def _has_alarm(detail: Mapping[str, object], station: str) -> bool:
    alarms = detail.get("alarms")
    if not isinstance(alarms, list):
        return False
    return any(
        isinstance(alarm, dict) and alarm.get("station") == station for alarm in alarms
    )


def _earlier_disturbance(
    detail: Mapping[str, object], station: str, since: str
) -> Mapping[str, object] | None:
    """The first episode at another station that began before this one did.

    Parsed rather than compared as strings. Every instant the analysis service emits is UTC
    with a `Z`, so the lexicographic comparison would agree today — and would start
    disagreeing, silently and in the one direction that matters, the first time an offset
    other than `Z` appeared in the same field.
    """
    timeline = detail.get("timeline")
    if not isinstance(timeline, list):
        return None
    start = _instant(since)
    earlier = [
        episode
        for episode in timeline
        if isinstance(episode, dict)
        and episode.get("station") != station
        and _instant(str(episode.get("from_ts", ""))) < start
    ]
    if not earlier:
        return None
    return min(earlier, key=lambda episode: _instant(str(episode.get("from_ts", ""))))


def _instant(value: str) -> datetime:
    """An ISO-8601 instant, or the far future.

    The far future rather than an exception: a timeline entry with no usable instant cannot
    be shown to precede anything, which is the conservative reading — DP-11 is refused
    rather than asserted on a timestamp nobody can order.
    """
    try:
        return datetime.fromisoformat(value)  # 3.11+ reads the Z suffix
    except ValueError:
        return datetime.max.replace(tzinfo=UTC)


def _cite_stop(
    stop: Mapping[str, object], documents: frozenset[str], procedure: str
) -> list[dict[str, object]]:
    """The stop, and the procedure that says how to read it — if it was actually read."""
    citations: list[dict[str, object]] = []
    identifier = stop.get("id")
    if identifier:
        citations.append({"kind": "stop", "id": str(identifier)})
    if procedure in documents:
        citations.append({"kind": "sop", "id": procedure})
    return citations


def _status_final(status: Mapping[str, object]) -> dict[str, object]:
    stations = status.get("stations")
    if not isinstance(stations, list) or not stations:
        return {"findings": [], "caveats": ["No station has reported a state."]}
    states = ", ".join(
        f"{entry.get('station')} {entry.get('state')}"
        for entry in stations
        if isinstance(entry, dict)
    )
    staleness = status.get("staleness_seconds")
    age = (
        f" The newest data is {_float(staleness):.0f} s old."
        if isinstance(staleness, int | float)
        else " Nothing has ever arrived, so there is no state to report."
    )
    return {
        "findings": [_finding(f"Station states: {states}.{age}", "measured", [])],
        "caveats": [],
    }


def _trend_final(trend: Mapping[str, object]) -> dict[str, object]:
    points = trend.get("points")
    if not isinstance(points, list) or not points:
        silent = (
            "That signal recorded nothing in the window, which is not the same as it "
            "having held steady."
        )
        return {"findings": [], "caveats": [silent]}
    values = [
        _float(point.get("value"))
        for point in points
        if isinstance(point, dict) and point.get("value") is not None
    ]
    if not values:
        return {"findings": [], "caveats": ["Every sample in the window was null."]}
    return {
        "findings": [
            _finding(
                f"{trend.get('signal')} at {trend.get('station')} ran from "
                f"{values[0]:.1f} to {values[-1]:.1f} over {len(values)} points.",
                "measured",
                [],
            )
        ],
        "caveats": [],
    }


def _part_final(part: Mapping[str, object] | None) -> dict[str, object]:
    if part is None:
        unnamed = "The question named no serial I could resolve, so there was nothing to trace."
        return {"findings": [], "caveats": [unnamed]}
    serial = str(part.get("assembly_serial", ""))
    inspection = part.get("inspection")
    verdict = inspection.get("result") if isinstance(inspection, dict) else None
    disposition = part.get("disposition")
    where = disposition.get("disposition") if isinstance(disposition, dict) else None
    return {
        "findings": [
            _finding(
                f"{serial} was inspected as {verdict} and dispositioned {where}.",
                "measured",
                [{"kind": "part", "id": serial}] if serial else [],
            )
        ],
        "caveats": [],
    }


def _span(window: Mapping[str, object]) -> str:
    start, end = window.get("from_ts"), window.get("to_ts")
    if start is None or end is None:
        return ""
    return f" between {_clock(start)} and {_clock(end)} UTC"


def _clock(value: object) -> str:
    text = str(value)
    return text[11:16] if len(text) >= 16 else text


def _int(value: object) -> int:
    return (
        int(value)
        if isinstance(value, int | float) and not isinstance(value, bool)
        else 0
    )


def _float(value: object) -> float:
    return (
        float(value)
        if isinstance(value, int | float) and not isinstance(value, bool)
        else 0.0
    )
