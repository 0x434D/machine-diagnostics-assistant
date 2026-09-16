"""Fixtures over fakes, for the same reason the agent's tests use them.

These tests are about what this binding puts on the wire. The queries themselves are tested
against real Postgres in the analysis package, and testing them again through a second
transport would be testing the database twice and the binding once.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from auth.testing import AUDIENCE, ISSUER, PUBLIC_PEM, mint
from knowledge.documents import DEFAULT_ROOT, KnowledgeBase
from mcp_server.config import Settings


@pytest.fixture(scope="session", autouse=True)
def authentication() -> Iterator[None]:
    """The server configured the way it is deployed: a public key, an audience, an issuer.

    Through the environment rather than by replacing the guard, for the reason the other two
    packages' conftests give: a suite that props the door open proves nothing about the door.
    """
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("AUTH_PUBLIC_KEY", PUBLIC_PEM)
        environment.setenv("AUTH_AUDIENCE", AUDIENCE)
        environment.setenv("AUTH_ISSUER", ISSUER)
        yield


@pytest.fixture(scope="session")
def bearer() -> str:
    """A `user` token. §10.5's matrix has no admin row this server serves."""
    return mint(role="user")


@pytest.fixture
def settings() -> Settings:
    """The defaults, with the two service URLs named explicitly.

    Named rather than inherited: a developer with MCP_ANALYSIS_URL exported would otherwise
    point these tests at their own deployment, and a parity test that silently compared
    against a different one would pass while proving nothing.
    """
    return Settings(analysis_url="http://analysis.test", agent_url="http://agent.test")


@pytest.fixture
def knowledge() -> KnowledgeBase:
    """The real tree. It is 40 documents of Markdown in this repository and the resources
    §6.11 exposes are those documents — a fixture tree would test a fixture."""
    return KnowledgeBase(DEFAULT_ROOT)
