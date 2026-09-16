"""§6.1 stage 5's surface: the fifteen analysis operations, reachable and correct.

Two claims are tested here. That the tool set and the committed OpenAPI contract name the
same operations — a tool the agent can call that the REST binding cannot is the drift
§6.11 forbids — and that each tool builds the request the contract describes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
import yaml
from agent.tools import OPERATIONS, TOOL_DEFINITIONS, AnalysisClient

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "analysis.openapi.yaml"

START = datetime(2026, 9, 12, 13, 30, tzinfo=UTC)
END = datetime(2026, 9, 12, 14, 30, tzinfo=UTC)


def _contract_operation_ids() -> set[str]:
    document = cast(dict[str, object], yaml.safe_load(CONTRACT.read_text()))
    paths = cast(dict[str, dict[str, dict[str, str]]], document["paths"])
    return {
        method["operationId"] for path in paths.values() for method in path.values()
    }


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[AnalysisClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(respond)
    return (
        AnalysisClient("http://analysis:8000", httpx.AsyncClient(transport=transport)),
        seen,
    )


def _ok(request: httpx.Request) -> httpx.Response:
    del request
    return httpx.Response(200, json={"ok": True})


def test_the_tool_set_and_the_contract_name_the_same_operations() -> None:
    """§6.11: neither binding can do anything the other cannot. `getKnowledgeDocument` is
    the one contract operation that is not a tool — §6.11 makes the knowledge base a
    *resource* rather than a query, and the agent already holds its documents by routing."""
    assert {operation.operation_id for operation in OPERATIONS} | {
        "getKnowledgeDocument"
    } == _contract_operation_ids()


def test_fifteen_operations_each_with_a_name_and_a_schema() -> None:
    assert len(TOOL_DEFINITIONS) == 15
    assert len({definition["name"] for definition in TOOL_DEFINITIONS}) == 15
    for definition in TOOL_DEFINITIONS:
        assert definition["description"]
        schema = definition["input_schema"]
        assert isinstance(schema, dict)
        assert schema["type"] == "object"


def test_no_tool_asks_the_model_for_a_time_window() -> None:
    """§6.1 step 2 splits for a reason, and this is where the split would leak back: a
    `from`/`to` in a tool schema is an invitation for the model to do date arithmetic.
    Code supplies the window to every windowed operation."""
    for definition in TOOL_DEFINITIONS:
        schema = cast(dict[str, object], definition["input_schema"])
        properties = cast(dict[str, object], schema["properties"])
        assert "from" not in properties
        assert "to" not in properties


@pytest.mark.parametrize(
    ("name", "arguments", "path", "query"),
    [
        (
            "resolve_time",
            {"expression": "last night"},
            "/time/resolve",
            {"expression": "last night"},
        ),
        ("coverage", {}, "/coverage", {}),
        ("list_stops", {}, "/stops", {}),
        ("get_stop", {"identifier": "s-17"}, "/stops/s-17", {}),
        ("list_alarms", {"station": "S2"}, "/alarms", {"station": "S2"}),
        (
            "signal_trend",
            {"station": "S2", "signal": "JoiningForce", "agg": "minute"},
            "/signals/trend",
            {"station": "S2", "signal": "JoiningForce", "agg": "minute"},
        ),
        (
            "inspection_stats",
            {"group_by": "carrier"},
            "/inspection/stats",
            {"group_by": "carrier"},
        ),
        ("inspection_patterns", {}, "/inspection/patterns", {}),
        ("line_status", {}, "/line/status", {}),
        ("get_part", {"serial": "A-00000007"}, "/parts/A-00000007", {}),
        ("affected_parts", {"carrier": 7}, "/parts/affected", {"carrier": "7"}),
        ("lot_parts", {"lot_code": "L-4471"}, "/lots/L-4471/parts", {}),
        ("component_assembly", {"serial": "C-1"}, "/components/C-1/assembly", {}),
        ("carrier_parts", {"carrier_id": 7}, "/carriers/7/parts", {}),
    ],
)
async def test_each_operation_builds_the_request_the_contract_describes(
    name: str,
    arguments: dict[str, object],
    path: str,
    query: dict[str, str],
) -> None:
    client, seen = _client(_ok)

    await client.call(name, arguments, START, END)

    assert seen[0].url.path == path
    for key, value in query.items():
        assert seen[0].url.params[key] == value


async def test_a_windowed_operation_carries_the_window_code_resolved() -> None:
    client, seen = _client(_ok)

    await client.call("list_stops", {}, START, END)

    assert seen[0].url.params["from"] == "2026-09-12T13:30:00Z"
    assert seen[0].url.params["to"] == "2026-09-12T14:30:00Z"


async def test_an_unwindowed_operation_is_not_given_one() -> None:
    client, seen = _client(_ok)

    await client.call("line_status", {}, START, END)

    assert "from" not in seen[0].url.params


async def test_the_image_operation_answers_with_what_it_is_rather_than_its_bytes() -> (
    None
):
    """§7.3 renders the image in the evidence panel; the model is told it exists and how
    to reach it. Handing a model a PNG is neither useful nor cheap."""

    def png(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200, content=b"\x89PNG\r\n", headers={"content-type": "image/png"}
        )

    client, _seen = _client(png)

    result = await client.call("get_part_image", {"serial": "A-00000007"}, START, END)

    assert result["media_type"] == "image/png"
    assert result["bytes"] == 6


async def test_a_refused_request_comes_back_as_a_result_not_an_exception() -> None:
    """§6.8: tool errors return to the model as tool results rather than crashing the
    request. The body matters — /time/resolve's 422 carries the expressions it *does*
    understand, which is what lets the model retry with something real."""

    def refuse(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            422, json={"detail": {"understood": ["last night", "this shift"]}}
        )

    client, _seen = _client(refuse)

    result = await client.call(
        "resolve_time", {"expression": "penultimate"}, START, END
    )

    assert result["error"] is True
    assert result["status"] == 422
    assert "last night" in str(result["detail"])


async def test_a_tool_that_does_not_exist_is_answered_not_raised() -> None:
    client, seen = _client(_ok)

    result = await client.call("drop_the_line", {}, START, END)

    assert result["error"] is True
    assert seen == []


async def test_a_missing_required_argument_is_answered_not_raised() -> None:
    client, seen = _client(_ok)

    result = await client.call("get_part", {}, START, END)

    assert result["error"] is True
    assert "serial" in str(result["detail"])
    assert seen == []


async def test_an_argument_the_operation_does_not_have_is_answered_not_raised() -> None:
    """A model that invents a parameter must be told so, not have it dropped: a filter
    silently ignored turns "parts on carrier 7" into "every part"."""
    client, seen = _client(_ok)

    result = await client.call("list_stops", {"carrier": 7}, START, END)

    assert result["error"] is True
    assert "carrier" in str(result["detail"])
    assert seen == []


async def test_resolve_time_reads_the_window_out_of_the_service() -> None:
    def resolution(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "expression": "last night",
                "window": {
                    "from_ts": "2026-09-11T20:00:00Z",
                    "to_ts": "2026-09-12T04:00:00Z",
                },
                "label": "night shift",
                "closed": True,
                "now": "2026-09-12T14:30:00Z",
            },
        )

    client, _seen = _client(resolution)

    window = await client.resolve_time("last night")

    assert window is not None
    assert window.start == datetime(2026, 9, 11, 20, 0, tzinfo=UTC)
    assert window.label == "night shift"


async def test_an_expression_the_calendar_does_not_know_resolves_to_nothing() -> None:
    """Never a guess: §6.1's resolver answers 422 rather than a near-miss window, and the
    agent's job is to say what it assumed instead."""

    def refuse(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(422, json={"detail": {"understood": []}})

    client, _seen = _client(refuse)

    assert await client.resolve_time("the day before the audit") is None
