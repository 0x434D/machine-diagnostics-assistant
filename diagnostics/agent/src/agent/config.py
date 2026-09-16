"""Every setting is configuration (§10.3), and no credential lives here."""

from __future__ import annotations

from pathlib import Path

from knowledge.documents import DEFAULT_ROOT
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent.routing import DEFAULT_BUDGET, RetrievalBudget


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_")

    analysis_url: str = "http://analysis:8000"

    # The agent's own schema, reached as the role that owns it (§8, and migration 0001).
    # `agent` holds `agent.*` and nothing else: no reach into `ingest`, no write to `read`.
    #
    # No password in the default, the way `analysis.config` states it: a local run that has
    # not been given one fails to authenticate rather than quietly reaching a database with
    # different rights. Alembic reads this same setting, so a migration and the service that
    # follows it can never be pointed at two different databases.
    database_url: str = "postgresql://agent@localhost:5432/diagnostics"

    # What the bootstrap gives the `agent` role to authenticate with. Migration 0001 creates
    # that role able to log in and with no password, the way 005 creates `analysis`, so
    # something privileged has to hand it one afterwards — and for `agent.*` that is whoever
    # applies the bootstrap revision. Same mechanism and same shape as the gateway's
    # GATEWAY_ANALYSIS_PASSWORD (Program.cs), not a second one.
    #
    # Empty by default and never committed: a role with no password cannot authenticate at
    # all, which is a better resting state than one every copy of this image shares.
    role_password: str = ""

    #: "scripted" or "anthropic". Scripted is the default because it needs no credentials
    #: and reaches no network; anthropic reads ANTHROPIC_API_KEY from the environment.
    provider: str = "scripted"

    #: §6.2's retrieval budget in its M1 form: the tool loop is bounded so a provider that
    #: keeps asking cannot run forever.
    tool_budget: int = 4

    # §6.2's knowledge base. Mounted at the repository's top level on a developer machine
    # and laid down at the same path inside the image, so neither has to be told.
    knowledge_root: Path = DEFAULT_ROOT

    # §6.2's *retrieval* budget, which is a different number from the tool budget above and
    # measures a different thing: context dilution rather than runtime. Two numbers because
    # §6.2 caps document count and total size, and a selection can breach either first.
    #
    # Defaulted off `routing.DEFAULT_BUDGET` rather than typed again, the way
    # `analysis.config` reads its numbers off the modules that define them: one number, and
    # an environment variable that moves it, rather than two that agree today.
    retrieval_documents: int = DEFAULT_BUDGET.documents
    retrieval_characters: int = DEFAULT_BUDGET.characters

    @property
    def retrieval_budget(self) -> RetrievalBudget:
        return RetrievalBudget(
            documents=self.retrieval_documents, characters=self.retrieval_characters
        )
