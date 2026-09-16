"""`/inspection/patterns`: §5.5 over HTTP, and the two answers that are not "look here".

`test_significance.py` and `test_patterns.py` prove the arithmetic against the plant's real
noise floor. What is left is the wiring, and in particular the three ways this endpoint can
decline to make a claim — below the sample gate, with no reference to compare against, and on
a dimension the line cannot distinguish at all. Each is a different sentence for the agent to
say, and a system that collapsed them into "not significant" would report a clean line where
nobody had checked.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from analysis.config import Settings
from analysis.patterns import Correction
from fastapi.testclient import TestClient

WINDOW = {"from": "2026-09-12T01:00:00Z", "to": "2026-09-12T02:00:00Z"}


def _dimension(
    body: dict[str, object], name: str, within: str | None = None
) -> dict[str, object]:
    """One section of the response, named by the pair that identifies it.

    `carrier` appears twice — on its own, and stratified within the defect class — and the
    two do not have the same answer, so a lookup by name alone would silently pick one.
    """
    dimensions = body["dimensions"]
    assert isinstance(dimensions, list)
    matching = [
        row
        for row in dimensions
        if row["dimension"] == name and row["within"] == within
    ]
    assert len(matching) == 1, f"expected one {name} within {within}, got {dimensions}"
    return dict(matching[0])


def _patterns(section: dict[str, object]) -> list[dict[str, object]]:
    """One dimension's values, typed enough to index into.

    `body` comes back as JSON and every step into it has to say what it expects — the same
    reason `test_contract.py` reads its file as `object` rather than `Any`.
    """
    rows = section["patterns"]
    assert isinstance(rows, list)
    return [dict(row) for row in rows]


@pytest.mark.usefixtures("seeded_db")
def test_every_dimension_section_five_five_names_is_reported(
    client: TestClient,
) -> None:
    body = client.get("/inspection/patterns", params=WINDOW).json()

    assert [(row["dimension"], row["within"]) for row in body["dimensions"]] == [
        ("carrier", None),
        ("lane", None),
        ("lot", None),
        ("defect_class", None),
        # §5.5 names four dimensions and none of them is a pair. §3.5 scenario 4 is a class
        # concentrated on a carrier, and `patterns.find_patterns` carries the measurement of
        # what testing the carrier on its own costs.
        ("carrier", "defect_class"),
        ("time_bucket", None),
    ]
    assert body["alpha"] == 0.05
    assert body["correction"] == "benjamini_hochberg"


@pytest.mark.usefixtures("seeded_db")
def test_the_lane_dimension_is_refused_rather_than_answered_not_significant(
    client: TestClient,
) -> None:
    """**The spec disagrees with itself here and this is the resolution.**

    §5.3 and §5.5 list `lane` as a dimension; §3.5 of the same document says every assembly
    draws one component from *each* feeder lane, so "these parts saw lane 2 and those did
    not" is a distinction the line cannot make. Testing it anyway would compare two groups
    holding the identical parts and return *not significant* — a true-sounding sentence
    about a comparison that was never made. The counts stay available at
    `/inspection/stats?group_by=lane`; what is withheld is a verdict on them.
    """
    lane = _dimension(client.get("/inspection/patterns", params=WINDOW).json(), "lane")

    assert lane["comparable"] is False
    assert _patterns(lane) == []
    # Null and not zero: no observations were built for a dimension nothing was computed
    # over, and a zero there is a measurement nobody made.
    assert lane["unattributed"] is None
    # §3.5's own words, quoted rather than paraphrased, so that the next reader can see the
    # specification already settled this and does not "fix" it into a silent negative.
    reason = str(lane["not_comparable"])
    assert "distinction the line cannot make" in reason
    assert "evidence the plant does not yet carry" in reason


@pytest.mark.usefixtures("seeded_db")
def test_a_dimension_below_the_sample_gate_says_it_could_not_look(
    client: TestClient,
) -> None:
    """Fifteen carriers over 600 parts is 40 each, against a gate of 100.

    `not_enough_data` and not `not_significant`, and the null p-value is what keeps them
    apart downstream: a number there would be an invitation to compare it against α
    somewhere, which is exactly the collapse the third verdict exists to prevent.
    """
    carrier = _dimension(
        client.get("/inspection/patterns", params=WINDOW).json(), "carrier"
    )

    assert {row["verdict"] for row in _patterns(carrier)} == {"not_enough_data"}
    assert {row["trials"] for row in _patterns(carrier)} == {40}
    assert all(row["p_value"] is None for row in _patterns(carrier))
    assert all(row["adjusted_p_value"] is None for row in _patterns(carrier))


@pytest.mark.usefixtures("seeded_db")
def test_a_single_valued_dimension_has_no_reference_to_compare_against(
    client: TestClient,
) -> None:
    """One hour, one hour-long bucket. Leave-one-out leaves nothing, and the honest answer is
    that we could not look — not that the hour was unremarkable."""
    buckets = _dimension(
        client.get("/inspection/patterns", params=WINDOW).json(), "time_bucket"
    )

    assert len(_patterns(buckets)) == 1
    assert _patterns(buckets)[0]["verdict"] == "not_enough_data"


@pytest.mark.usefixtures("seeded_db")
def test_nothing_significant_is_the_expected_answer_and_is_a_number(
    client: TestClient,
) -> None:
    """*"Which means `/inspection/patterns` can return nothing, and mean it."*

    `significant_count` is carried so that "nothing" is a field rather than an absence a
    reader has to notice by scanning four lists.
    """
    body = client.get("/inspection/patterns", params=WINDOW).json()

    assert body["significant_count"] == 0


def test_a_real_effect_is_found_once_the_gate_admits_the_sample(
    seeded_db: str,
    analysis_url: str,
    tuned_client: Callable[[Settings], TestClient],
) -> None:
    """The fixture puts every reject on one of three carriers — 10 in 40 against 20 in 560.

    The gate is configuration (§10.3) and moving it is what makes this testable at all at
    the fixture's scale; the effect it then finds is the fixture's own construction, so the
    assertion is that the endpoint reports the carriers the rejects are actually on.
    """
    assert seeded_db, "the counts below are this fixture's, not whatever ran before"
    client = tuned_client(
        Settings(database_url=analysis_url, significance_minimum_sample=10)
    )
    carrier = _dimension(
        client.get("/inspection/patterns", params=WINDOW).json(), "carrier"
    )

    significant = [row for row in _patterns(carrier) if row["verdict"] == "significant"]
    assert {row["value"] for row in significant} == {"3", "8", "13"}
    worst = significant[0]
    assert worst["observed"] == 10
    assert worst["trials"] == 40
    assert worst["observed_share"] == pytest.approx(0.25)
    assert worst["expected_share"] == pytest.approx(20 / 560)
    assert worst["effect_size"] is not None
    assert worst["adjusted_p_value"] is not None


@pytest.mark.usefixtures("seeded_db")
def test_the_defect_class_denominator_is_the_part_and_matches_the_stats_endpoint(
    client: TestClient,
) -> None:
    """**The denominator decision, asserted in both places at once.**

    A part is one trial for each class, never a fraction of a trial shared between them:
    §3.4's six scores are independent, most parts carry none and one here carries two. So
    the denominator under every class is every inspected part in the window — 600 — and the
    numerator is the parts that reached the threshold on that class.

    The same two numbers have to come back from `/inspection/stats?group_by=defect_class`,
    because a reader comparing the endpoints would otherwise find two different shares for
    one fact.
    """
    patterns = _dimension(
        client.get("/inspection/patterns", params=WINDOW).json(), "defect_class"
    )
    groups = client.get(
        "/inspection/stats", params={**WINDOW, "group_by": "defect_class"}
    ).json()["groups"]

    by_class = {
        row["value"]: (row["observed"], row["trials"]) for row in _patterns(patterns)
    }
    assert by_class["gap"] == (5, 600)
    # The part that carries two classes at once is why this one is six against five.
    assert by_class["scratch"] == (6, 600)

    from_stats = {row["key"]: (row["rejects"], row["parts"]) for row in groups}
    assert from_stats == by_class


@pytest.mark.usefixtures("seeded_db_with_gap")
def test_the_report_carries_the_coverage_of_the_window_it_tested(
    client: TestClient,
) -> None:
    """A window the gateway was down for produces a smaller sample, and a smaller sample is
    exactly what turns a real effect into `not_enough_data`. The reader has to be able to
    see which of the two happened."""
    body = client.get("/inspection/patterns", params=WINDOW).json()

    assert body["coverage"]["fully_covered"] is False


@pytest.mark.usefixtures("seeded_db")
def test_the_lot_dimension_reads_the_genealogy_and_not_the_clock(
    client: TestClient,
) -> None:
    """**The dimension §5.5 does not list and §3.5 scenario 7 cannot be answered without.**

    Scenario 7 is a run of rising `gap` defects with a *perfectly stable* joining force. The
    symptom points straight at a press drift, and the only thing separating that wrong
    answer from the right one is that the defects correlate with the supplier lot. Nothing
    can measure that correlation unless the lot is a dimension.

    The three counts below are the falsifier. `LOT-A1` and `LOT-A2` each went into 300 parts
    and `LOT-B1` into all 600, because a part contains one component from each lane and is
    one trial for each lot it contains. A read path that worked out "which lot was current
    when this part was made" could not produce that shape at all — the two lanes' boundaries
    are staggered on purpose (§3.5), and `LOT-A2`'s first parts were created before it was
    loaded.
    """
    lot = _dimension(client.get("/inspection/patterns", params=WINDOW).json(), "lot")

    assert lot["comparable"] is True
    trials = {row["value"]: row["trials"] for row in _patterns(lot)}
    assert trials == {"LOT-A1": 300, "LOT-A2": 300, "LOT-B1": 600}
    # Every lot at the same 5 % share, which is what a line where the lot explains nothing
    # looks like — and the answer this endpoint has to be able to give.
    assert {row["verdict"] for row in _patterns(lot)} == {"not_significant"}


@pytest.mark.usefixtures("seeded_db_with_unaccounted_rejects")
def test_a_part_whose_lot_is_unknown_is_counted_rather_than_dropped(
    client: TestClient,
) -> None:
    """Two rejects here have no genealogy at all, so no lot can be attributed to them.

    Counted as unattributed rather than left out of the query: a dimension quietly computed
    over fewer parts than the window holds is the one shape the sample gate cannot protect
    against, because it reads as a clean and well-powered answer.
    """
    lot = _dimension(client.get("/inspection/patterns", params=WINDOW).json(), "lot")

    assert lot["unattributed"] == 2


def test_the_multiplicity_correction_is_configuration(
    seeded_db: str,
    analysis_url: str,
    tuned_client: Callable[[Settings], TestClient],
) -> None:
    """§10.3: it is a number that changes what the service claims.

    Benjamini-Hochberg bounds the share of the reported findings that are false; Bonferroni
    bounds the chance of any false finding at all; `none` is what the other two were
    measured against and is what this asserts, because with no correction every adjusted
    p-value is its own raw one and the comparison is exact rather than directional.
    """
    assert seeded_db, "the counts below are this fixture's, not whatever ran before"
    uncorrected = tuned_client(
        Settings(
            database_url=analysis_url,
            significance_minimum_sample=10,
            pattern_correction=Correction.NONE,
        )
    )
    body = uncorrected.get("/inspection/patterns", params=WINDOW).json()
    carrier = _dimension(body, "carrier")

    assert body["correction"] == "none"
    tested = [row for row in _patterns(carrier) if row["p_value"] is not None]
    assert tested
    assert all(row["adjusted_p_value"] == row["p_value"] for row in tested)


def test_the_corrected_report_is_never_stronger_than_the_uncorrected_one(
    seeded_db: str,
    analysis_url: str,
    tuned_client: Callable[[Settings], TestClient],
) -> None:
    """Fifteen carriers tested at once is fifteen chances to find something. The correction
    is what stops that becoming a finding on a line where nothing is wrong, and the price is
    paid in every adjusted p-value being at or above its raw one."""
    assert seeded_db, "the counts below are this fixture's, not whatever ran before"
    corrected = tuned_client(
        Settings(
            database_url=analysis_url,
            significance_minimum_sample=10,
            pattern_correction=Correction.BENJAMINI_HOCHBERG,
        )
    )
    carrier = _dimension(
        corrected.get("/inspection/patterns", params=WINDOW).json(), "carrier"
    )

    tested = [row for row in _patterns(carrier) if row["p_value"] is not None]
    assert tested
    assert all(
        float(str(row["adjusted_p_value"])) >= float(str(row["p_value"]))
        for row in tested
    )
