"""Every setting is configuration (§10.3), and no credential lives here.

There is none to live here: this server holds no database grant of its own. It reaches the
analysis service and the agent over HTTP, exactly as any other client of theirs does, which
is the point — §6.11's claim is about a binding, not about a second privileged path into
the data.
"""

from __future__ import annotations

from pathlib import Path

from knowledge.documents import DEFAULT_ROOT
from pydantic_settings import BaseSettings, SettingsConfigDict

from mcp_server.contract import DEFAULT_CONTRACT

PROTOCOL_REVISION = "2026-07-28"
"""The MCP specification revision this server is written and tested against (§6.11).

§6.11 asks for the revision to be pinned explicitly, and names this one: it reworked
Streamable HTTP substantially, removing protocol-level sessions, the standalone GET stream
and `Last-Event-ID` resumption. That removal is why it is the right target here rather than
merely the newest one — a read-only query surface holds nothing per client between calls, so
a self-contained POST is what this server actually is, and a session id it would have to
mint, store and expire would be state invented to satisfy a transport.

**The handshake era is not refused, and that is deliberate.** The SDK's one ASGI app serves
both, and the clients that exist today still send `initialize` — the SDK's own default
negotiated version is 2025-03-26 and the newest handshake revision is 2025-11-25. §6.11's
claim is that the method transfers to *an agent we did not build*; a server that only spoke
the newest revision would make that claim untestable against almost every client there is.
`tests/test_transport.py` drives both eras and asserts they see the same tool list.
"""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MCP_")

    # Where the two services this server binds over are. Both plain HTTP on `diag-net`, and
    # both read-only from here: there is no method in this package that writes anything.
    analysis_url: str = "http://analysis:8000"
    agent_url: str = "http://agent:8001"

    # §6.11: bound to localhost. The default is the loopback address rather than 0.0.0.0 so
    # that a run outside the container -- where nothing else constrains it -- is bound the
    # way the spec asks. compose.yml sets 0.0.0.0 for the container, whose port is published
    # only to 127.0.0.1 on the host and which sits on an internal network besides.
    host: str = "127.0.0.1"
    port: int = 8002
    #: The Streamable HTTP endpoint path. One POST per request; there is no GET stream in
    #: the pinned revision and this server opens none.
    path: str = "/mcp"

    # §10.3: two timeouts because they bound two different things. An analysis query is a
    # database read and a slow one means something is wrong; `diagnose` runs the whole staged
    # pipeline, which is several tool calls and a model, and a timeout short enough for the
    # first would abort every one of the second.
    analysis_timeout_seconds: float = 30.0
    diagnose_timeout_seconds: float = 300.0

    # §6.2's knowledge base, as the resources §6.11 exposes. Same default and same
    # environment override as the other two readers, so all three can be pointed at one tree.
    knowledge_root: Path = DEFAULT_ROOT

    #: The contract both bindings come from. Configuration because the image lays it down at
    #: a path the source-tree arithmetic cannot reach.
    contract: Path = DEFAULT_CONTRACT
