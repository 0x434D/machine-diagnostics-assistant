"""§6.5: every cited id is resolved against the database before the answer ships.

Two properties are tested here, and they are not the same one. That each kind in §7.3's
vocabulary reaches an endpoint that can say yes or no — a kind that cannot be resolved is
not a kind — and that a cited **procedure** is checked against what routing actually
loaded, which is a stronger claim than that the document exists.

What none of this tests is whether a *model* would cite honestly. That is behavioural, it
needs a model, and no test in this package can reach it.
"""

from __future__ import annotations

from agent.answer import Citation, Finding
from agent.citations import keep, resolves
from agent.tools import AnalysisClient

from .fakes import WINDOWS, FakeAnalysis, alarm_list, pattern_report

WINDOW = WINDOWS["last hour"]
ROUTED = frozenset({"SOP-01", "CORE-01", "CORE-02", "DP-11"})


def _client(fake: FakeAnalysis) -> AnalysisClient:
    """The fake satisfies the methods `citations.py` uses; this keeps the annotation honest
    without inventing a Protocol that exists only for a test."""
    return fake  # type: ignore[return-value]  # a Protocol here would exist only for tests


def _finding(*citations: Citation, basis: str = "measured") -> Finding:
    return Finding(
        statement="a statement.",
        # See test_compose.py: pydantic enforces the Literal at construction, so widening
        # the parameter here loses no checking.
        basis=basis,  # type: ignore[arg-type]  # the Literal is enforced at runtime
        citations=list(citations),
    )


async def _resolves(citation: Citation, fake: FakeAnalysis) -> bool:
    return await resolves(citation, _client(fake), window=WINDOW, loaded=ROUTED)


# --- every kind reaches an endpoint ----------------------------------------------------


async def test_a_part_resolves_against_the_part_endpoint() -> None:
    fake = FakeAnalysis(known={"A-00000007"})

    assert await _resolves(Citation(kind="part", id="A-00000007"), fake)
    assert not await _resolves(Citation(kind="part", id="A-99999999"), fake)
    assert ("resolve:get_part", {"serial": "A-00000007"}) in fake.calls


async def test_a_component_serial_resolves_against_its_own_endpoint() -> None:
    """`serial` is a *component* serial, which is a different referent from an assembly
    serial and has an endpoint of its own. §7.3's example sends it to /parts/ — see the
    report; read that way it is `part` spelled twice."""
    fake = FakeAnalysis(components={"C-1"})

    assert await _resolves(Citation(kind="serial", id="C-1"), fake)
    assert not await _resolves(Citation(kind="serial", id="C-nope"), fake)
    assert ("resolve:component_assembly", {"serial": "C-1"}) in fake.calls


async def test_a_stop_resolves_against_the_stop_endpoint() -> None:
    from .fakes import stop, stop_list

    fake = FakeAnalysis({"list_stops": stop_list(stop("s-1", 660.0))})

    assert await _resolves(Citation(kind="stop", id="s-1"), fake)
    assert not await _resolves(Citation(kind="stop", id="s-19"), fake)


async def test_a_lot_resolves_against_the_lot_endpoint() -> None:
    fake = FakeAnalysis(lots={"L-4471"})

    assert await _resolves(Citation(kind="lot", id="L-4471"), fake)
    assert not await _resolves(Citation(kind="lot", id="L-0000"), fake)


async def test_a_signal_resolves_its_station() -> None:
    """Its station, and not its name. §5.3 answers an unknown signal name with an empty
    series rather than a 404, so "S2 publishes no JoiningForce" and "S2's JoiningForce was
    silent this hour" arrive identically — and treating an empty series as a failed
    citation would strip the true claim that a signal went quiet."""
    fake = FakeAnalysis(stations={"S2"})

    assert await _resolves(
        Citation(kind="signal", station="S2", signal="JoiningForce"), fake
    )
    assert not await _resolves(
        Citation(kind="signal", station="S9", signal="JoiningForce"), fake
    )


async def test_an_alarm_resolves_by_membership_in_the_window() -> None:
    """There is no `GET /alarms/{id}`, so the window's own answer is what an alarm id is
    checked against."""
    fake = FakeAnalysis({"list_alarms": alarm_list(207)})

    assert await _resolves(Citation(kind="alarm", id="207"), fake)
    assert not await _resolves(Citation(kind="alarm", id="999"), fake)


async def test_a_pattern_resolves_by_the_cell_existing() -> None:
    """Existing, not being significant: a citation supporting "there was no pattern on
    carrier 3" points at the cell that says so."""
    fake = FakeAnalysis({"inspection_patterns": pattern_report("carrier", "7")})

    assert await _resolves(Citation(kind="pattern", dimension="carrier", key="7"), fake)
    assert not await _resolves(
        Citation(kind="pattern", dimension="carrier", key="3"), fake
    )
    assert not await _resolves(
        Citation(kind="pattern", dimension="lane", key="1"), fake
    )


async def test_two_containment_scopes_are_two_referents_even_if_they_render_alike() -> (
    None
):
    """A containment citation has no id, so it cannot be matched by the label a reader
    sees. Matching by label would let one failing scope silently take another claim's
    finding with it — different evidence, same sentence in the caveat."""
    fake = FakeAnalysis(known={"A-00000007", "A-00000008"})

    checked = await keep(
        [
            _finding(Citation(kind="containment", serials=["A-00000007"])),
            _finding(Citation(kind="containment", serials=["A-99999999"])),
        ],
        _client(fake),
        window=WINDOW,
        loaded=ROUTED,
    )

    assert len(checked.kept) == 1
    assert checked.kept[0].citations[0].serials == ["A-00000007"]


async def test_a_containment_scope_resolves_every_serial_in_it() -> None:
    """Every serial is a real part. Whether it is the *right* set — no misses, no false
    inclusions — is §8.1's containment class, scored against ground truth."""
    fake = FakeAnalysis(known={"A-00000007", "A-00000008"})

    assert await _resolves(
        Citation(kind="containment", serials=["A-00000007", "A-00000008"]), fake
    )
    assert not await _resolves(
        Citation(kind="containment", serials=["A-00000007", "A-99999999"]), fake
    )


# --- the check that is not "does it exist" ---------------------------------------------


async def test_a_cited_procedure_must_have_been_routed_not_merely_exist() -> None:
    """§6.5: "Cited SOPs are additionally checked against the set routing actually loaded,
    so the model cannot invent a procedure it never read."

    DP-02 is a real document in the tree — `knowledge_exists` says so — and it was not
    routed for this question. Citing it claims a procedure the model never saw, which reads
    exactly like a correct citation and is not one.
    """
    fake = FakeAnalysis()

    assert await _resolves(Citation(kind="sop", id="SOP-01"), fake)
    assert not await _resolves(Citation(kind="sop", id="DP-02"), fake)


async def test_a_routed_procedure_that_does_not_exist_is_still_refused() -> None:
    """Both halves. A document id nothing serves is not made real by having been asked
    for."""
    fake = FakeAnalysis()

    assert not await resolves(
        Citation(kind="sop", id="SOP-99"),
        _client(fake),
        window=WINDOW,
        loaded=frozenset({"SOP-99"}),
    )


# --- what removal does, and what must never cause it -----------------------------------


async def test_unresolvable_claims_are_removed_and_the_answer_says_so() -> None:
    """After the retry §6.5 allows, the offending claims go and the answer carries a
    visible note — the removal is never silent."""
    fake = FakeAnalysis(known={"A-00000007"})

    checked = await keep(
        [
            _finding(Citation(kind="part", id="A-00000007")),
            _finding(Citation(kind="part", id="A-99999999")),
        ],
        _client(fake),
        window=WINDOW,
        loaded=ROUTED,
    )

    assert len(checked.kept) == 1
    assert checked.kept[0].citations[0].id == "A-00000007"
    assert checked.failed == ["part:A-99999999"]
    assert checked.note is not None
    assert "could not be verified" in checked.note


async def test_a_fully_verified_set_is_returned_unchanged() -> None:
    """The other direction, so the check is not passing by removing everything."""
    fake = FakeAnalysis(known={"A-00000007"})
    findings = [_finding(Citation(kind="part", id="A-00000007"))]

    checked = await keep(findings, _client(fake), window=WINDOW, loaded=ROUTED)

    assert checked.kept == findings
    assert checked.failed == []
    assert checked.note is None


async def test_a_window_scoped_report_is_read_once_per_pass() -> None:
    """An answer citing eight alarms would otherwise read the same window eight times, and
    the second read could disagree with the first while the answer was being checked."""
    fake = FakeAnalysis({"list_alarms": alarm_list(207, 208)})

    await keep(
        [
            _finding(Citation(kind="alarm", id="207")),
            _finding(Citation(kind="alarm", id="208")),
        ],
        _client(fake),
        window=WINDOW,
        loaded=ROUTED,
    )

    assert [name for name, _ in fake.calls].count("fetch:list_alarms") == 1
