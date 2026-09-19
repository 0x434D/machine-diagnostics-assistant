"""§6.3's answer object. Nothing is parsed out of prose — the model produces this shape
through a tool call, and every sentence a user reads traces back to a verified finding.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Basis = Literal["measured", "derived", "hypothesis"]

# §6.3: `hypothesis` means the claim required interpretation from the knowledge base.
# Routing now loads that knowledge (§6.2, and `method.sops_used` records which documents),
# so an interpretation has something behind it and something to be checked against — which
# is what `False` was waiting for. The constraint underneath the flag does not move: a
# hypothesis without `evidence_strength` still raises, because a label claiming
# interpretation without stating what supports it is the invention this flag was guarding.
ALLOW_HYPOTHESIS = True

Kind = Literal[
    "part",
    "stop",
    "alarm",
    "signal",
    "pattern",
    "sop",
    "serial",
    "lot",
    "containment",
    "chart",
]
"""§7.3's vocabulary, every kind of which resolves against something that can say no.

`chart` arrives at M6 with the renderer §7.4 describes. It is the one kind whose referent
is not a row in the database but a **tool call this run made**: §7.4's whole design is that
a chart references a verified tool result and never carries values, so what makes a chart
citation resolvable is the recorded call, and `agent.citations` checks it against exactly
that (§7.2's trace is where the UI then reaches the stored result).

Each kind resolves against a real endpoint or a real recorded call (`agent.citations`), and
a kind that could not be resolved would not be a kind: an id that cannot be opened is, in
the spec's words, barely a citation.
"""

ChartType = Literal[
    "timeseries",
    "state_gantt",
    "pareto",
    "stacked_bar",
    "rate_over_time",
    "summary_tiles",
    "vega_lite",
]
"""§7.4's six built-in types, and the free-form fallback they fall through to.

Menu first: the six cover the everyday questions and each one names a reading of a tool
result rather than a drawing. `vega_lite` is the fallback §7.4 specifies — declarative data
rather than code, and therefore safe to accept from a model — and it is the only one that
carries a specification of its own.
"""

#: What each kind must carry for its resolver to run, and — by exclusion — what it must not.
#: One table, two uses: `Citation` validates against it, and `agent.citations` dispatches on
#: it, so a kind cannot be added to the vocabulary without a resolver noticing.
CITATION_FIELDS: dict[Kind, tuple[str, ...]] = {
    "part": ("id",),
    "stop": ("id",),
    "alarm": ("id",),
    "signal": ("station", "signal"),
    "pattern": ("dimension", "key"),
    "sop": ("id",),
    "serial": ("id",),
    "lot": ("id",),
    "containment": ("serials",),
    "chart": ("chart_type", "source", "options"),
}

_PAYLOAD: tuple[str, ...] = (
    "id",
    "station",
    "signal",
    "dimension",
    "key",
    "serials",
    "chart_type",
    "source",
    "options",
)

WINDOWED: frozenset[Kind] = frozenset({"pattern", "signal", "chart"})
"""The kinds §7.3 writes a `window` into, which are the ones whose referent needs one.

A pattern cell and a signal series do not exist on their own — `/inspection/patterns` and
`/signals/trend` both take `from` and `to`, and the same `carrier=7` is a different cell over
a different interval. A chart is the same case drawn rather than tabulated: every tool result
a chart reads was computed over this run's window, and an axis labelled with any other
interval is the wrong-figures failure §7.4 exists to refuse, moved from the values to the
scale. Every other kind names a referent the database holds once.
"""

_DATA_KEYS = frozenset({"data", "datasets"})
"""Every way a Vega-Lite specification can carry values: `data.values`, `data.url`,
`data.name` and the top-level `datasets` block. Refusing both keys at every depth is what
makes "the specification never carries values" a property of the object rather than a rule
somebody has to remember while reviewing a prompt."""


class CitationWindow(BaseModel):
    """The interval a claim was made over, carried by the citation that backs it (§7.3).

    **Written by the pipeline, never by the model.** §6.1 step 2 resolves the question's time
    phrase through `/time/resolve`; `agent.citations.stamped` copies that window onto the
    citations that take one and clears it from the rest, so anything a model put here is
    overwritten before the answer is built. A model that could type a window into a citation
    would be typing data onto the panel the citation opens — §7.4's failure, in a table
    rather than a chart, and it reads more authoritatively than a wrong sentence.

    `label` is the calendar's own phrasing of the same interval, which is what §6.1 has the
    answer quote back: a reader checking a panel against the answer reads "night shift
    2026-09-11 22:00 – 06:00 Europe/Berlin", not two ISO instants.
    """

    from_ts: datetime
    to_ts: datetime
    label: str


def _carries_data(value: object) -> str | None:
    """The path to the first `data` or `datasets` key anywhere in a Vega-Lite spec.

    Recursive because Vega-Lite nests: a `layer`, a `concat` or a `facet` each holds whole
    specifications, and a top-level check would pass a spec whose second layer carried the
    invented numbers. Returns the path so the refusal can say where, rather than making the
    author diff two objects to find out.
    """
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in _DATA_KEYS:
                return str(key)
            found = _carries_data(nested)
            if found is not None:
                return f"{key}.{found}"
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _carries_data(item)
            if found is not None:
                return f"[{index}].{found}"
    return None


class ChartOptions(BaseModel):
    """§7.4's *"it never carries values the model typed"*, as a shape rather than a rule.

    **Every field here is a string**, and that is the whole guarantee: a name is not a
    number, so there is no field on this object into which a model could type a figure or a
    series of them. What the model chooses is the *reading* — which path in the tool result
    holds the rows, which of their fields is the x channel — and never the rows themselves.
    A wrong choice draws a chart of the wrong column of real data, which is visible; an
    invented figure draws a chart of nothing, which is not, and reads more authoritatively
    than a wrong sentence.

    `spec` is the one exception in type and is not one in substance: the free-form fallback
    is a whole Vega-Lite object, and the validator below refuses `data` and `datasets` at
    every depth of it. The renderer binds the verified tool result in their place.

    `bands` is §7.4's *"stop and alarm periods shaded"*: a **second tool call id**, so the
    shading is as referenced as the series under it. Closed to extra fields, because an
    option this does not know is either a typo the model will not be told about or a channel
    nothing renders.
    """

    model_config = ConfigDict(extra="forbid")

    series: str | None = None
    """Dotted path from the tool result to the array of rows — `points`, `groups`."""

    x: str | None = None
    y: str | None = None
    colour: str | None = None
    """The field to split, stack or colour by. For `state_gantt` it is the field holding
    the PackML state name, which the renderer maps to §15's five categories itself — the
    category is a reading of the state and is not the model's to assign."""

    end: str | None = None
    """The field closing an interval, for the two types whose marks have a width in time."""

    title: str | None = None

    bands: str | None = None
    """A second tool call id, whose rows shade the periods behind a `timeseries`."""

    bands_series: str | None = None
    bands_x: str | None = None
    bands_end: str | None = None

    spec: dict[str, object] | None = None
    """`vega_lite` only: a Vega-Lite specification with no data of any kind in it."""

    @model_validator(mode="after")
    def _check_spec_carries_no_data(self) -> ChartOptions:
        if self.spec is None:
            return self
        found = _carries_data(self.spec)
        if found is not None:
            raise ValueError(
                f"a chart specification references a tool result and never carries "
                f"values (§7.4); remove {found!r} from the Vega-Lite spec"
            )
        return self


class Citation(BaseModel):
    """§7.3's typed citation: a kind, and exactly the payload that kind's endpoint needs.

    One model with a per-kind payload rather than one class per kind in a discriminated
    union. The union is the more literal encoding of §7.3 and was weighed: it would give
    the frontend a generated type per kind, of which M4 renders none — M6 builds the
    evidence panel — in exchange for a constructor per kind here. The validators below give
    the same guarantee the union would (a `stop` citation without an id cannot exist) in one
    place, and §6.3 asks for these checks to be structural, which this is.

    **`id` rather than §7.3's three spellings.** The spec writes `id` for a stop, `value`
    for a serial and `lot_code` for a lot. They are one concept — the single string that
    identifies the referent — and three names for it would be three renderer paths and
    three resolver signatures for no difference in meaning. The composite kinds keep their
    own fields, because those genuinely are composites.
    """

    kind: Kind
    id: str | None = None
    station: str | None = None
    signal: str | None = None
    dimension: str | None = None
    key: str | None = None
    serials: list[str] | None = None
    #: §7.3's chart citation, spelled as §7.3 spells it: a type, a tool call id, and the
    #: options that say how to read that call's stored result. `source` is an id and never
    #: a tool name — one run may call `inspection_stats` three times with three arguments,
    #: and a chart that named the tool would be a chart of whichever of the three the
    #: renderer happened to find.
    chart_type: ChartType | None = None
    source: str | None = None
    options: ChartOptions | None = None
    #: §7.3's window, and the one part of a citation the model does not write. Absent from
    #: `CITATION_FIELDS` and from `_PAYLOAD` deliberately: that table is what the model must
    #: supply and must not exceed, and this is filled in afterwards from the run's own
    #: resolved window. `Answer` below is where a `pattern` or `signal` citation that never
    #: got one is refused, which is the check the model's output cannot be subject to.
    window: CitationWindow | None = None

    @model_validator(mode="after")
    def _check_payload(self) -> Citation:
        required = CITATION_FIELDS[self.kind]
        missing = [name for name in required if not getattr(self, name)]
        if missing:
            raise ValueError(
                f"a {self.kind!r} citation needs {', '.join(required)}; "
                f"{', '.join(missing)} missing"
            )
        # A `part` citation carrying a station would render one thing and resolve another.
        extra = [
            name
            for name in _PAYLOAD
            if name not in required and getattr(self, name) is not None
        ]
        if extra:
            raise ValueError(
                f"a {self.kind!r} citation carries only {', '.join(required)}; "
                f"remove {', '.join(extra)}"
            )
        return self

    @model_validator(mode="after")
    def _check_chart(self) -> Citation:
        """The menu and the fallback are alternatives, and each needs its own options.

        §7.4 is *"menu first, free description as fallback"*. A built-in carrying a
        Vega-Lite spec has chosen both, and the renderer would have to pick one — which is
        the renderer deciding what the answer meant.
        """
        if self.chart_type is None or self.options is None:
            return self
        # Every type, the fallback included: §7.4's data comes from the referenced tool
        # result, so every chart has to say where in that result its rows are. A fallback
        # with no path to rows would be a specification with nothing to bind to, which is
        # the one thing a Vega-Lite spec is allowed to be and this one is not.
        if not self.options.series:
            raise ValueError(
                f"a {self.chart_type!r} chart needs options.series — the path to the rows "
                f"inside the tool result it references"
            )
        if self.chart_type == "vega_lite":
            if self.options.spec is None:
                raise ValueError(
                    "the 'vega_lite' fallback is its specification; options.spec is missing"
                )
            return self
        if self.options.spec is not None:
            raise ValueError(
                f"a {self.chart_type!r} chart is one of §7.4's built-in types and is "
                f"drawn from its own options; remove options.spec, or use 'vega_lite'"
            )
        return self

    @property
    def label(self) -> str:
        """How this citation is named in a note about it. Not an identifier: two citations
        with the same label are the same referent, which is all the note needs."""
        if self.kind == "signal":
            return f"signal:{self.station}/{self.signal}"
        if self.kind == "pattern":
            return f"pattern:{self.dimension}={self.key}"
        if self.kind == "containment":
            return f"containment:{len(self.serials or [])} serial(s)"
        if self.kind == "chart":
            return f"chart:{self.chart_type} from {self.source}"
        return f"{self.kind}:{self.id}"


class Finding(BaseModel):
    statement: str
    basis: Basis
    citations: list[Citation] = Field(default_factory=list)
    evidence_strength: str | None = None

    @model_validator(mode="after")
    def _check_basis(self) -> Finding:
        validate_basis(
            self.basis, self.evidence_strength, allow_hypothesis=ALLOW_HYPOTHESIS
        )
        return self


class Method(BaseModel):
    """§6.3's record of how the answer was reached, and what it cost.

    `budget_used` counts **turns of the tool loop**, which is the thing §6.1 step 5's budget
    caps — not tool calls, which `tools_called` already lists and whose count is its length.
    One field carrying two units is a number nobody can compare across two answers.
    """

    sops_used: list[str] = Field(default_factory=list)
    tools_called: list[str] = Field(default_factory=list)
    budget_used: int = 0
    # Which provider produced this answer. Recorded because a simulated answer and a real
    # one are not the same claim, and the difference must survive into the trace.
    provider: str = "unknown"


class Clarification(BaseModel):
    """§6.7's one sanctioned question back, as an object rather than a sentence.

    Asking is a refusal to investigate, so it has to be as inspectable as an answer: §8.1
    scores "asked when it should not have" as its own case class, and a question hidden
    inside `answer_markdown` could only be scored by reading prose. `readings` are the
    investigations that would have differed — fewer than two of them is not ambiguity, and
    the validator says so rather than letting a caveat ship as a question.
    """

    question: str
    readings: list[str]

    @model_validator(mode="after")
    def _check_readings(self) -> Clarification:
        if len(self.readings) < 2:
            raise ValueError(
                "a clarification needs at least two readings (\u00a76.7): one reading is "
                "an assumption to state in a caveat, not a question to ask"
            )
        return self


class Contradiction(BaseModel):
    """§6.5's first-class disagreement with the computed propagation.

    > The agent receiving the propagation derivation may disagree with it [...] and must
    > then populate `contradiction` with its own root and its reasoning.

    Possible at all only because §5.4 returns the chain as data rather than a verdict: a
    root with no derivation behind it could be disagreed with but not argued against.

    **A contradiction without reasoning is invalid**, and refused here rather than left to
    a prompt — for the same reason a `hypothesis` without `evidence_strength` is refused.
    A bare "the root is elsewhere" is not visible disagreement; it is a second unexplained
    verdict standing beside the first, and the reader has no way to choose between them.
    DP-11: "Never quietly answer with a different root than the one you were handed —
    silent disagreement is indistinguishable from an error."
    """

    derived_root: str
    agent_root: str
    reasoning: str

    @model_validator(mode="after")
    def _check_reasoning(self) -> Contradiction:
        blank = [
            name
            for name in ("derived_root", "agent_root", "reasoning")
            if not getattr(self, name).strip()
        ]
        if blank:
            raise ValueError(
                f"a contradiction states both roots and the reasoning (\u00a76.5); "
                f"{', '.join(blank)} is empty"
            )
        if self.derived_root == self.agent_root:
            # Then nothing is being contradicted, and the field would render a
            # disagreement the UI shows prominently where there is none.
            raise ValueError(
                "a contradiction whose roots agree contradicts nothing (\u00a76.5)"
            )
        return self


class Answer(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    answer_markdown: str
    method: Method
    caveats: list[str] = Field(default_factory=list)
    contradiction: Contradiction | None = None
    clarification: Clarification | None = None

    @model_validator(mode="after")
    def _check_clarification(self) -> Answer:
        # §6.7 makes asking and answering alternatives: "ask back *or* assume the most
        # likely one and say so". An answer carrying both has investigated a question it
        # claimed it could not read, and the reader has no way to know which half to trust.
        if self.clarification is not None and self.findings:
            raise ValueError(
                "an answer that asks back has no findings (\u00a76.7): it either "
                "investigated or it did not"
            )
        return self

    @model_validator(mode="after")
    def _check_windows(self) -> Answer:
        """\u00a77.3's window, checked where the model's own output cannot be checked.

        A `pattern` or `signal` citation without one opens onto an interval the reader's
        screen chose \u2014 real rows from the database, under a sentence they do not support.
        `agent.citations.stamped` is what fills it in; this is what fails if it stops.
        """
        missing = [
            citation.label
            for finding in self.findings
            for citation in finding.citations
            if citation.kind in WINDOWED and citation.window is None
        ]
        if missing:
            raise ValueError(
                f"a {', '.join(sorted(WINDOWED))} citation carries the window its claim "
                f"was made over (\u00a77.3); {', '.join(missing)} carries none"
            )
        return self


def validate_basis(
    basis: Basis, evidence_strength: str | None, *, allow_hypothesis: bool
) -> None:
    """§6.3: evidence_strength is required whenever basis is hypothesis, and its presence
    is checked structurally rather than asked for in a prompt.

    Presence, and deliberately not content. §6.3 asks the strength to state the support in
    figures — *"92 % of misalignment defects on carrier 7, n=214, p<0.001"* — and a check
    for a digit would pass "1 of my hunches" while failing "n = two hundred and fourteen".
    Whether the figures are the right ones is §8.1's hypothesis-labelling class, scored
    against ground truth by something that can read them.
    """
    if basis == "hypothesis":
        if not allow_hypothesis:
            raise ValueError(
                "basis 'hypothesis' requires a knowledge base to interpret from; without "
                "routed knowledge a hypothesis would be an invention"
            )
        if not evidence_strength:
            raise ValueError("basis 'hypothesis' requires evidence_strength (§6.3)")


#: The committed contract carries a dialect and a human title that Pydantic does not emit.
#: They live here rather than being applied by hand to the generated file, so that
#: regenerating is reproducible and tests/test_contract.py has something exact to compare
#: against. A hand-decorated contract is a contract nothing can check.
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_TITLE = "machine-agent answer object (\u00a76.3)"


def json_schema() -> dict[str, object]:
    """§6.3's answer object as JSON Schema. The UI generates its types from this."""
    return {
        "$schema": SCHEMA_DIALECT,
        **Answer.model_json_schema(),
        "title": SCHEMA_TITLE,
    }
