"""Connecting to this server the way a client with a token does.

Since M5 the server refuses an unauthenticated request (§10.5, §14), so every test that
drives it over HTTP has to carry one. `Client` takes a URL or a transport; a header needs
the transport form, and `streamable_http_client` takes the HTTP client to send with — which
is `httpx2`, the SDK's own, and a different distribution from the `httpx` this package pins
for its other uses rather than an upgrade of it.
"""

from __future__ import annotations

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_server.config import PROTOCOL_REVISION

TIMEOUT_SECONDS = 120.0


def authorised(endpoint: str, token: str, mode: str = PROTOCOL_REVISION) -> Client:
    """One client, with the token on every request it makes."""
    return Client(
        streamable_http_client(
            endpoint,
            http_client=httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT_SECONDS
            ),
        ),
        mode=mode,
    )
