"""§5.3's containment endpoints: which parts a condition touched, and where they went.

*"340 serials, 62 rejected, 278 shipped and need checking."* `test_traceability.py` proves
the rule these rest on — the per-part record is read by serial and never reconstructed from
a time range — and `test_containment.py` proves the three-way split. What is left here is
the wiring: which per-part instant a window is applied to, which questions this cannot
answer at all, and that 404, 422 and an empty scope are three different answers.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

TRACED = "A-00000123"
"""The same ordinary part `test_traceability.py` traces, reached here from its component."""

WINDOW = {"from": "2026-09-12T01:00:00Z", "to": "2026-09-12T02:00:00Z"}


@pytest.mark.usefixtures("seeded_db")
def test_the_containment_scope_separates_rejected_from_shipped(
    client: TestClient,
) -> None:
    """*"The answer a plant needs at three in the morning."*

    Of the parts made in this hour, thirty were thrown out, five hundred and sixty-eight
    left the line intact, and two are still on it — the twins, created and pressed and not
    yet inspected. A total alone would be the same number with the actionable part removed.
    """
    body = client.get("/parts/affected", params=WINDOW).json()

    assert body["parts"]["total"] == 600
    assert body["parts"]["rejected"]["count"] == 30
    assert body["parts"]["shipped"]["count"] == 568
    assert body["parts"]["on_the_line"]["count"] == 2
    assert body["criteria"]["anchor"] == "created"


@pytest.mark.usefixtures("seeded_db")
def test_a_part_the_window_cannot_be_applied_to_is_counted_rather_than_dropped(
    client: TestClient,
) -> None:
    """`A-HORIZON` was created before the gateway's history horizon, so it has no creation
    instant and no window can place it. It is not outside the window; it is a part the
    window cannot be applied to, and a containment list silently short is worse than one
    that says it is."""
    body = client.get("/parts/affected", params=WINDOW).json()

    assert body["unplaceable"] == 1


@pytest.mark.usefixtures("seeded_db")
def test_the_press_cannot_be_asked_for_a_window_and_says_so(
    client: TestClient,
) -> None:
    """**§5.3's own worked example is the one this cannot answer, and it refuses rather than
    guessing.**

    The press records two numbers against the serial and no instant beside them, and
    `part_station_events` — the table that would have carried one — is empty by design.
    Reaching for the creation instant instead would answer "which parts passed S2 while the
    force was out of tolerance" with "which parts were *created* around then", which is an
    inference across a buffer and is the association §3.4a says must never be reconstructed.
    A 422 naming the reason is worth more than a list somebody acts on.
    """
    response = client.get("/parts/affected", params={**WINDOW, "station": "S2"})

    assert response.status_code == 422
    assert "§3.4a" in response.json()["detail"]


@pytest.mark.usefixtures("seeded_db")
def test_a_station_that_records_an_instant_anchors_the_window_on_its_own_record(
    client: TestClient,
) -> None:
    """S3 stamps every verdict, so "passed S3 in this hour" is answerable exactly — and its
    answer is a different set from S1's, which is the point of naming a station at all."""
    body = client.get("/parts/affected", params={**WINDOW, "station": "S3"}).json()

    assert body["criteria"]["anchor"] == "inspected"
    assert body["parts"]["total"] == 600
    assert body["parts"]["on_the_line"]["count"] == 0
    # The twins and A-STUBLOT have no verdict at all, so no window places them under this
    # anchor -- and they are counted rather than vanishing.
    assert body["unplaceable"] == 3


@pytest.mark.usefixtures("seeded_db")
def test_a_station_the_line_does_not_have_is_a_404(client: TestClient) -> None:
    """Three different refusals, because they call for three different next steps: 404 for a
    station that does not exist, 422 for one that records no instant, and an empty scope for
    criteria that simply matched nothing."""
    assert (
        client.get("/parts/affected", params={**WINDOW, "station": "S9"}).status_code
        == 404
    )


@pytest.mark.usefixtures("seeded_db")
def test_a_criterion_that_matches_nothing_is_an_empty_scope_and_not_an_error(
    client: TestClient,
) -> None:
    body = client.get(
        "/parts/affected", params={**WINDOW, "defect_class": "corrosion"}
    ).json()

    assert body["parts"]["total"] == 0
    assert body["parts"]["rejected"]["serials"] == []


@pytest.mark.usefixtures("seeded_db")
def test_a_defect_class_criterion_reads_the_score_vector(client: TestClient) -> None:
    """Six parts carry `scratch` and five carry `gap`, and the sixth scratch is the part the
    model believes carries two defects at once — the same asymmetry `/inspection/stats`
    reports, reached here through a per-part predicate rather than a count."""
    scratches = client.get(
        "/parts/affected", params={**WINDOW, "defect_class": "scratch"}
    ).json()
    gaps = client.get(
        "/parts/affected", params={**WINDOW, "defect_class": "gap"}
    ).json()

    assert scratches["parts"]["rejected"]["count"] == 6
    assert gaps["parts"]["rejected"]["count"] == 5
    assert "A-00000007" in scratches["parts"]["rejected"]["serials"]
    assert "A-00000007" in gaps["parts"]["rejected"]["serials"]


@pytest.mark.usefixtures("seeded_db")
def test_two_lots_loaded_at_the_same_instant_answer_with_different_parts(
    client: TestClient,
) -> None:
    """**The no-time-join guard for containment.**

    `LOT-A1` and `LOT-B1` were both loaded before the window, on different lanes. A read
    path that worked out "which lot was current when this part was made" would give the two
    codes the same set of parts; the genealogy gives them 300 and 601. Nothing but the
    per-part component record can tell them apart.
    """
    lane_one = client.get("/lots/LOT-A1/parts").json()
    lane_two = client.get("/lots/LOT-B1/parts").json()

    assert lane_one["parts"]["total"] == 300
    assert lane_two["parts"]["total"] == 601
    assert [lot["lane"] for lot in lane_one["lots"]] == [1]
    assert [lot["supplier"] for lot in lane_two["lots"]] == ["Borealis"]


@pytest.mark.usefixtures("seeded_db")
def test_a_lot_split_by_its_window_still_reads_its_own_parts(
    client: TestClient,
) -> None:
    """`LOT-A2` replaces `LOT-A1` on lane 1 halfway through the window, and its first parts
    were created *before* it was loaded — the lot boundary is a consumption boundary, not a
    clock one. A window applied to the part's own creation instant gets 300; a reconstruction
    from `loaded_at` would get 298."""
    body = client.get("/lots/LOT-A2/parts").json()

    assert body["parts"]["total"] == 300


@pytest.mark.usefixtures("seeded_db")
def test_a_lot_code_nothing_carries_is_a_404(client: TestClient) -> None:
    assert client.get("/lots/LOT-NOTHING/parts").status_code == 404


@pytest.mark.usefixtures("seeded_db")
def test_one_component_serial_resolves_to_the_assembly_it_went_into(
    client: TestClient,
) -> None:
    """§5.3's single-component recall: a supplier finds a defect months later and gives you
    one serial. Only answerable because components are individually serialised."""
    body = client.get("/components/C-1-00000123/assembly").json()

    assert body["assembly_serial"] == TRACED
    assert body["lane"] == 1
    assert body["lot"]["lot_code"] == "LOT-A1"
    assert body["disposition"]["disposition"] == "good"


@pytest.mark.usefixtures("seeded_db")
def test_a_component_not_yet_built_into_anything_is_not_a_404(
    client: TestClient,
) -> None:
    """A null assembly is a real state — read at a feeder, not yet consumed — and a 404
    would have reported it as a serial nobody has heard of (§6.5)."""
    body = client.get("/components/C-1-STUB/assembly").json()

    assert body["assembly_serial"] == "A-STUBLOT"
    # Read before the gateway's horizon, so everything about it but its existence is unknown.
    assert body["lane"] is None
    assert body["lot"] is None
    assert body["read_at"] is None


@pytest.mark.usefixtures("seeded_db")
def test_a_component_serial_nothing_carries_is_a_404(client: TestClient) -> None:
    assert client.get("/components/C-9-99999999/assembly").status_code == 404


@pytest.mark.usefixtures("seeded_db")
def test_a_carrier_answers_with_every_part_that_rode_it(client: TestClient) -> None:
    """§3.1 keeps the carriers in a closed loop, so this is a list over many passes rather
    than one — which is also what makes carrier wear detectable at all."""
    body = client.get("/carriers/8/parts").json()

    assert body["parts"]["total"] == 40
    assert body["parts"]["rejected"]["count"] == 10
    assert body["window"] is None


@pytest.mark.usefixtures("seeded_db")
def test_a_carrier_the_line_does_not_have_is_a_404(client: TestClient) -> None:
    assert client.get("/carriers/999/parts").status_code == 404


@pytest.mark.usefixtures("seeded_db")
def test_half_a_window_is_refused_rather_than_completed_for_the_caller(
    client: TestClient,
) -> None:
    """A caller that sent `from` alone meant to bound the answer, and would otherwise get
    the whole history back believing it was bounded."""
    response = client.get("/carriers/8/parts", params={"from": WINDOW["from"]})

    assert response.status_code == 422
