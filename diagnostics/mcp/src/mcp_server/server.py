"""§6.11's server: Streamable HTTP, bound to localhost, on `diag-net`, never on `field-net`.

stdio is ruled out by the spec and by arithmetic: it requires the client to spawn the server
as a subprocess, and a subprocess of a client outside this stack is not a container on
`diag-net`. This server therefore speaks HTTP and only HTTP, and the transport is the one
place where "an agent we did not build" stops being a figure of speech.

The lowlevel `Server` is used rather than the SDK's decorator front end for one reason: every
tool here is generated from `contracts/analysis.openapi.yaml`, and the decorator front end
derives a tool's schema from a Python function's signature. Generating functions to be
introspected back into the schemas we already hold would put a lossy round trip between the
contract and the wire — which is the seam §6.11's parity test exists to forbid.
"""

from __future__ import annotations

from collections.abc import Mapping

import mcp_types as types
import uvicorn
from knowledge.documents import KnowledgeBase
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import Server
from mcp.shared.exceptions import MCPError
from starlette.applications import Starlette

from mcp_server import contract, resources
from mcp_server.config import Settings
from mcp_server.diagnose import DIAGNOSE_TOOL, AgentClient
from mcp_server.diagnose import tool as diagnose_tool
from mcp_server.tools import AnalysisTools

NAME = "machine-agent diagnostics"

INSTRUCTIONS = f"""\
Read-only access to a production line's recorded history and to the knowledge base its
diagnostics are written against. Nothing here can act on the plant.

Start with the resources: `sop://SOP-01` is the line-stop procedure, and `core://CORE-01`
and `core://CORE-02` are the method and the evidence rules that every procedure assumes —
read those two first, whatever the question. The tools are the same analysis queries the
procedures tell you to run, under the same names the REST API uses. `{DIAGNOSE_TOOL}` runs
the whole staged pipeline instead and returns its answer object with every citation already
resolved.

Windows are half-open and in UTC. Use `resolveTime` for a phrase like "last night" rather
than computing it, and `coverage` before concluding anything from a count: a window with an
ingest gap yields an incomplete number, not a small one.\
"""


def build(settings: Settings | None = None) -> Server[object]:
    """The MCP server, with its tools generated and its resources read off disk.

    `Settings` is the only seam, and deliberately the only one: the two collaborators reach
    their services by URL, so a test that wants to stand one of them up points a setting at
    it. A constructor parameter per collaborator would be a second way to assemble the same
    server, and the one the gate exercises would stop being the one that runs.
    """
    settings = settings or Settings()
    tools = AnalysisTools(
        contract.load(settings.contract),
        settings.analysis_url,
        settings.analysis_timeout_seconds,
    )
    pipeline = AgentClient(settings.agent_url, settings.diagnose_timeout_seconds)
    documents = KnowledgeBase(settings.knowledge_root)

    async def on_list_tools(
        ctx: ServerRequestContext[object, object],
        params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        del ctx, params
        return types.ListToolsResult(tools=[*tools.tools(), diagnose_tool()])

    async def on_call_tool(
        ctx: ServerRequestContext[object, object], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        del ctx
        arguments: Mapping[str, object] = params.arguments or {}
        if params.name == DIAGNOSE_TOOL:
            return await pipeline.diagnose(
                _string(arguments, "question"),
                _optional_string(arguments, "session_id"),
            )
        if params.name not in tools.names:
            raise MCPError(types.METHOD_NOT_FOUND, f"no tool named {params.name!r}")
        return await tools.call(params.name, arguments)

    async def on_list_resources(
        ctx: ServerRequestContext[object, object],
        params: types.PaginatedRequestParams | None,
    ) -> types.ListResourcesResult:
        del ctx, params
        # §6.2 hot-reloads the tree, so an SOP edited while tuning is visible on the next
        # listing without a restart. The reload swaps an immutable index.
        return types.ListResourcesResult(
            resources=resources.resources_for(documents.reload_if_changed())
        )

    async def on_read_resource(
        ctx: ServerRequestContext[object, object],
        params: types.ReadResourceRequestParams,
    ) -> types.ReadResourceResult:
        del ctx
        index = documents.reload_if_changed()
        try:
            return resources.read(index, params.uri)
        except resources.ResourceError as error:
            # Specific recovery, in CLAUDE.md's sense: "nothing is at that URI" is an answer
            # the protocol has a code for, and the caller can act on it. Nothing else is
            # caught here, and this re-raises rather than logging and continuing.
            raise MCPError(types.INVALID_PARAMS, str(error)) from error

    return Server(
        NAME,
        version="0.1.0",
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_resources=on_list_resources,
        on_read_resource=on_read_resource,
    )


def build_app(settings: Settings | None = None) -> Starlette:
    """The ASGI application. One POST per request; the pinned revision has no GET stream."""
    settings = settings or Settings()
    return build(settings).streamable_http_app(
        streamable_http_path=settings.path,
        # No `event_store`: `Last-Event-ID` resumption is one of the things the revision
        # pinned in config.PROTOCOL_REVISION removed, and a store for it would be machinery
        # kept for a feature that no longer exists.
        host=settings.host,
    )


def main() -> None:
    settings = Settings()
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port)


def _string(arguments: Mapping[str, object], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MCPError(types.INVALID_PARAMS, f"{key} must be a non-empty string")
    return value


def _optional_string(arguments: Mapping[str, object], key: str) -> str | None:
    if arguments.get(key) is None:
        return None
    return _string(arguments, key)


__all__ = ["INSTRUCTIONS", "NAME", "build", "build_app", "main"]
