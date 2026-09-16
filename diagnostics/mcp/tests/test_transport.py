"""§6.11's transport: Streamable HTTP, bound to localhost, over a real socket.

These tests bind a port rather than dispatching in process, because the claim being made is
about a transport. §6.11 says stdio does not work across a container boundary and HTTP does;
an in-process assertion would hold equally well if the HTTP half were broken, and the whole
point of this server is that a client we did not write can reach it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import uvicorn
from mcp_server import server
from mcp_server.config import PROTOCOL_REVISION, Settings
from mcp_server.diagnose import DIAGNOSE_TOOL
from mcp_types.version import (
    HANDSHAKE_PROTOCOL_VERSIONS,
    LATEST_MODERN_VERSION,
    MODERN_PROTOCOL_VERSIONS,
)

from .clients import authorised


@pytest_asyncio.fixture
async def endpoint(settings: Settings) -> AsyncIterator[str]:
    """The server on an ephemeral loopback port, for the length of one test.

    Port 0 rather than the configured one: the gate must not fail because a developer has
    something on 8002, and §10.3's point is that the number is configuration, not that this
    particular number is free.
    """
    config = uvicorn.Config(
        server.build_app(settings), host="127.0.0.1", port=0, log_level="warning"
    )
    running = uvicorn.Server(config)
    bound = config.bind_socket()
    port = int(bound.getsockname()[1])

    serving = asyncio.create_task(running.serve(sockets=[bound]))
    while not running.started:
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}{settings.path}"
    finally:
        running.should_exit = True
        await serving


async def test_a_client_reaches_the_tools_over_the_pinned_revision(
    endpoint: str, bearer: str
) -> None:
    """§6.11's revision, adopted directly: a self-contained POST, no handshake.

    `mode` is the SDK's word for "adopt this protocol version without probing for it", so
    what this drives is the 2026-07-28 exchange and nothing else.
    """
    async with authorised(endpoint, bearer) as client:
        listing = await client.list_tools()

    assert DIAGNOSE_TOOL in {tool.name for tool in listing.tools}


async def test_the_handshake_era_reaches_exactly_the_same_tools(
    endpoint: str, bearer: str
) -> None:
    """The clients that exist today still send `initialize`, and are not refused.

    §6.11's claim is that the method transfers to an agent we did not build. A server that
    spoke only the newest revision would be one that almost nothing can connect to, which
    makes that claim unfalsifiable rather than true — so both eras are served, and this
    asserts they see one surface rather than two.
    """
    async with authorised(endpoint, bearer) as modern:
        pinned = {tool.name for tool in (await modern.list_tools()).tools}
    async with authorised(endpoint, bearer, mode="legacy") as handshake:
        legacy = {tool.name for tool in (await handshake.list_tools()).tools}

    assert pinned == legacy


async def test_a_client_reads_an_sop_by_its_uri(endpoint: str, bearer: str) -> None:
    """The sentence §6.11 rests on: *an external agent can read `sop://SOP-01`*.

    Over the wire, because the URI survives a round trip is part of the claim: a scheme
    whose authority a client normalises would turn `SOP-01` into `sop-01` and resolve
    nothing.
    """
    async with authorised(endpoint, bearer) as client:
        result = await client.read_resource("sop://SOP-01")

    contents = result.contents[0]
    assert contents.uri == "sop://SOP-01"
    assert "SOP-01" in getattr(contents, "text", "")


def test_the_pinned_revision_is_what_the_sdk_calls_current() -> None:
    """A pin nothing checks is a comment.

    §6.11 names 2026-07-28, and this asserts the SDK still means the same thing by it. An
    SDK bump that introduced a newer per-request revision would otherwise leave this pin
    quietly describing the previous one.
    """
    assert PROTOCOL_REVISION == LATEST_MODERN_VERSION
    assert PROTOCOL_REVISION in MODERN_PROTOCOL_VERSIONS
    # The removal §6.11 calls out is what makes this the right pin rather than merely the
    # newest: protocol-level sessions live in the handshake era, and this is not in it.
    assert PROTOCOL_REVISION not in HANDSHAKE_PROTOCOL_VERSIONS


def test_the_default_bind_is_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """§6.11: bound to localhost, on `diag-net`, never on `field-net`.

    The container overrides the host because it must listen on its own interface, and its
    published port is loopback-only on the host besides. A run from a checkout gets the
    spec's answer without being told.
    """
    monkeypatch.delenv("MCP_HOST", raising=False)

    assert Settings().host == "127.0.0.1"


@pytest.mark.parametrize(
    ("variable", "value", "field", "expected"),
    [
        ("MCP_PORT", "9002", "port", 9002),
        ("MCP_ANALYSIS_TIMEOUT_SECONDS", "5", "analysis_timeout_seconds", 5.0),
        ("MCP_DIAGNOSE_TIMEOUT_SECONDS", "45", "diagnose_timeout_seconds", 45.0),
        ("MCP_PATH", "/tools", "path", "/tools"),
    ],
)
def test_every_number_is_configuration(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
    field: str,
    expected: object,
) -> None:
    """§10.3, asserted by moving each one through the environment rather than by reading
    the defaults back out of the class that declares them."""
    monkeypatch.setenv(variable, value)

    assert getattr(Settings(), field) == expected
