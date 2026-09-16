"""§6.11's test: one contract, two bindings, and neither can do anything the other cannot.

> *"The OpenAPI spec defines the capabilities; the frontend binds over REST, agent runtimes
> bind over MCP, both generated from one source. A test asserts that the MCP tool list and
> the OpenAPI operation set derive from the same definition. Neither binding can do anything
> the other cannot."*

Both sides of the comparison below are **live surfaces**, not files:

* the MCP tool list is what a client gets back from `tools/list` — so it has been through
  the generator, the registration and the protocol;
* the OpenAPI operation set is what `analysis.app` serves at `/openapi.json` — so it is the
  REST binding as it actually exists, not the committed YAML describing it.

Comparing the generated list against the YAML it was generated from would be the generator
agreeing with itself, which is the failure mode this test is written to avoid. The committed
file is still what joins them: `analysis/tests/test_contract.py` holds served == committed,
and this holds committed == served-over-MCP, so a change to either binding that is not
propagated breaks one of the two.

That is also why this package takes a test-scope dependency on `analysis`. The running
server never imports it — it reaches the service over HTTP like any other client.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from analysis.app import app as analysis_app
from fastapi.testclient import TestClient
from mcp import Client
from mcp_server import contract, server
from mcp_server.config import Settings
from mcp_server.diagnose import DIAGNOSE_TOOL


def served_operation_ids() -> set[str]:
    """Every operation the REST binding actually serves."""
    schema = TestClient(analysis_app).get("/openapi.json").json()
    return {
        operation["operationId"]
        for item in schema["paths"].values()
        for operation in item.values()
    }


async def served_tool_names(settings: Settings) -> set[str]:
    """Every tool the MCP binding actually serves, fetched over the protocol."""
    async with Client(server.build(settings)) as client:
        listing = await client.list_tools()
    return {tool.name for tool in listing.tools}


async def test_the_two_bindings_expose_the_same_operations(settings: Settings) -> None:
    """The assertion §6.11 asks for, as a set equality in both directions.

    `diagnose` is removed first because §6.11 lists it as its own primitive beside `tools`
    and `resources` — it is the staged pipeline, not an analysis query, and the agent's
    service is not in the analysis contract. The test below pins it to exactly that one
    exception, so it cannot become a hole to add capabilities through.
    """
    assert await served_tool_names(settings) - {DIAGNOSE_TOOL} == served_operation_ids()


async def test_diagnose_is_the_only_tool_that_is_not_a_contract_operation(
    settings: Settings,
) -> None:
    """Otherwise the exception above would be an unbounded escape hatch.

    A hand-written tool added beside the generated ones fails here rather than joining
    them — which is the difference between a list that is generated and a list that merely
    happens to match today.
    """
    assert await served_tool_names(settings) - served_operation_ids() == {DIAGNOSE_TOOL}


async def test_every_tool_says_it_is_read_only(settings: Settings) -> None:
    """§6.11 exposes the analysis queries and nothing that writes.

    Asserted on what the wire carries rather than on the annotation constant, because it is
    the wire a calling runtime reads before deciding whether a tool needs confirmation.
    """
    async with Client(server.build(settings)) as client:
        listing = await client.list_tools()

    for tool in listing.tools:
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name


def test_an_operation_that_writes_never_becomes_a_tool(tmp_path: Path) -> None:
    """Read-only is enforced at load, not promised in prose.

    §6.11's *"there is no plant-side MCP"* is a boundary the whole evaluation rests on, and
    the thing that would erode it is a POST added to the analysis service by someone who
    never read this file. The generator refuses one before the server starts.
    """
    written = tmp_path / "with-a-write.yaml"
    written.write_text(
        "paths:\n"
        "  /parts/{serial}:\n"
        "    post:\n"
        "      operationId: scrapPart\n"
        "      summary: Scrap a part\n"
        "      responses:\n"
        "        '200':\n"
        "          content:\n"
        "            application/json: {}\n"
    )

    with pytest.raises(contract.ContractError, match="nothing that writes"):
        contract.load(written)


def test_the_generated_tools_carry_the_contracts_own_parameter_schemas(
    settings: Settings,
) -> None:
    """A tool list that agrees on names and disagrees on arguments is still two bindings.

    Checked against `/signals/trend` because it is the operation with the most to lose: a
    required station, a required signal, a required half-open window and an aggregation
    enum that decides whether the answer is raw samples or buckets.
    """
    operations = {
        operation.name: operation for operation in contract.load(settings.contract)
    }
    schema = operations["signalTrend"].input_schema
    properties = schema["properties"]
    assert isinstance(properties, dict)

    assert set(properties) == {"station", "signal", "agg", "from", "to"}
    assert schema["required"] == ["station", "signal", "from", "to"]
    assert properties["agg"]["enum"] == ["raw", "minute", "hour"]
    assert properties["from"]["format"] == "date-time"
