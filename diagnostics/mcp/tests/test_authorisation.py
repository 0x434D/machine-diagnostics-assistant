"""§1.8 for the MCP server, which §14 names specifically.

The tools are read-only and read-only is not public. The list of them is fetched from the
server itself rather than written down here, for the reason the two FastAPI services'
copies of this file give: a tool added tomorrow joins this parametrisation without anyone
editing this file, where a hand-written list would go on passing beside it.

The refusals are driven with a plain HTTP client rather than the MCP one, and deliberately:
what a caller without a token meets is an HTTP 401, before any of this server's protocol
handling runs. An MCP client would be the wrong instrument for looking at that.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
import uvicorn
from mcp import Client
from mcp_server import server
from mcp_server.config import PROTOCOL_REVISION, Settings

from .clients import authorised

OPEN: frozenset[str] = frozenset()
"""Deliberate exceptions — methods this server answers without a token. There are none.

The guard is a middleware, so this holds for the whole surface at once: `initialize`, the
listings, every tool call, and the paths the server does not serve at all."""


def _rpc(method: str, params: dict[str, object] | None = None) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}


HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": PROTOCOL_REVISION,
}


@pytest_asyncio.fixture
async def endpoint(settings: Settings) -> AsyncIterator[str]:
    """The server on an ephemeral loopback port, for the length of one test."""
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


async def tool_names(settings: Settings) -> list[str]:
    """Every tool this server serves, asked of the server."""
    async with Client(server.build(settings)) as client:
        listing = await client.list_tools()
    return sorted(tool.name for tool in listing.tools)


async def test_the_server_serves_the_tools_this_file_then_holds_to_account(
    settings: Settings,
) -> None:
    """A parametrisation over an empty list passes loudest of all. §5.3's operations, plus
    §6.11's `diagnose`."""
    assert len(await tool_names(settings)) > 15


async def test_every_tool_refuses_an_unauthenticated_call(
    settings: Settings, endpoint: str
) -> None:
    """Enumerated from the server, one HTTP POST each, no token on any of them."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        for name in await tool_names(settings):
            response = await client.post(
                endpoint,
                json=_rpc("tools/call", {"name": name, "arguments": {}}),
                headers=HEADERS,
            )

            assert name not in OPEN
            assert response.status_code == 401, f"{name} was called without a token"
            assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "method", ["initialize", "tools/list", "resources/list", "server/discover"]
)
async def test_the_protocol_itself_refuses_an_unauthenticated_request(
    endpoint: str, method: str
) -> None:
    """Not only the calls: a client that cannot authenticate does not get as far as
    learning what this server can do."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(endpoint, json=_rpc(method), headers=HEADERS)

    assert response.status_code == 401


async def test_a_path_this_server_does_not_serve_is_refused_too(endpoint: str) -> None:
    """A 404 handed out without a token is an answer about which paths exist. The guard
    stands at the door, so there is none to hand out."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(endpoint.replace("/mcp", "/not-a-path"))

    assert response.status_code == 401


async def test_a_user_token_reaches_the_tools_and_the_resources(
    endpoint: str, bearer: str
) -> None:
    """The other half, without which every test above is satisfied by a server that is
    simply broken — and §10.5's matrix at the same time: this server has no admin row, so
    `user` is enough for all of it. Every tool here is one of the read operations the matrix
    puts in both columns, and `diagnose` is asking a question."""
    async with authorised(endpoint, bearer) as client:
        listing = await client.list_tools()
        resources = await client.list_resources()

    assert "resolveTime" in {tool.name for tool in listing.tools}
    assert resources.resources
