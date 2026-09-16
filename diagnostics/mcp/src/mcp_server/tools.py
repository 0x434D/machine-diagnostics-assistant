"""The contract's operations as MCP tools, and the call that serves one.

Every tool here is a GET against the analysis service. `contract.load` has already refused
anything else, so "read-only" is a property of what was loaded rather than a rule this
module remembers to follow — and `readOnlyHint` on each tool tells a calling model the same
thing in the vocabulary its own runtime understands.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import cast

import httpx
import mcp_types as types

from mcp_server.contract import Operation

JSON_MEDIA_TYPE = "application/json"

READ_ONLY = types.ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    # The analysis service answers from a database that the plant keeps writing to, so a
    # repeated call over an open window is not guaranteed to return what it did before.
    open_world_hint=True,
)
"""What every analysis tool is. §6.11 exposes the queries and nothing that writes."""


def tool_for(operation: Operation) -> types.Tool:
    """The MCP tool an operation becomes. The name is its `operationId`, unchanged."""
    return types.Tool(
        name=operation.name,
        description=operation.description,
        input_schema=operation.input_schema,
        annotations=READ_ONLY,
    )


class AnalysisTools:
    """Dispatch for the generated tools: one GET each, against the analysis service.

    The client is injectable for the same reason `agent.tools.AnalysisClient` makes it so —
    a test drives the real request path against an ASGI application instead of a socket.
    """

    def __init__(
        self,
        operations: tuple[Operation, ...],
        base_url: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._operations = {operation.name: operation for operation in operations}
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._client = client

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._operations)

    def tools(self) -> list[types.Tool]:
        return [tool_for(operation) for operation in self._operations.values()]

    async def call(
        self, name: str, arguments: Mapping[str, object]
    ) -> types.CallToolResult:
        """One operation, called. Raises `KeyError` for a name this server does not serve.

        A 4xx from the analysis service comes back as a tool error rather than an exception:
        *no part with that serial* is an answer the calling agent can act on — §6.5's whole
        citation check rests on being able to tell a missing id from a broken service — and
        turning it into a protocol error would take that distinction away from it. A
        connection failure is not an answer and is left to propagate.
        """
        operation = self._operations[name]
        path, query = operation.url_for(arguments)
        response = await self._get(path, query)

        if response.status_code >= 400:
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"{name} returned HTTP {response.status_code}: {response.text}",
                    )
                ],
                is_error=True,
            )

        if operation.media_type != JSON_MEDIA_TYPE:
            # `/parts/{serial}/image` is the case: a PNG handed to a model as text is noise,
            # and the image content block is what MCP has for exactly this.
            return types.CallToolResult(
                content=[
                    types.ImageContent(
                        type="image",
                        data=base64.b64encode(response.content).decode("ascii"),
                        mime_type=operation.media_type,
                    )
                ]
            )

        body = cast(object, response.json())
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=response.text)],
            # The same object the REST binding returns, in the field a runtime reads
            # structurally. §6.11's claim is that both bindings reach the same capability;
            # handing one of them only prose would make that false in the one place it is
            # easiest not to notice.
            #
            # Guarded because `structuredContent` is defined as an object: every §5.3
            # endpoint returns one today (test_tools.py holds that), and a future one that
            # returns a list would otherwise put a protocol violation on the wire rather
            # than falling back to the text block that already carries it.
            structured_content=body if isinstance(body, dict) else None,
        )

    async def _get(self, path: str, query: Mapping[str, str]) -> httpx.Response:
        url = f"{self._base_url}{path}"
        if self._client is not None:
            return await self._client.get(url, params=dict(query))
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.get(url, params=dict(query))
