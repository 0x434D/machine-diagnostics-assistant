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

    #: §6.1 step 5's budget: the tool loop is bounded so a provider that keeps asking
    #: cannot run forever. Exhausting it produces a partial answer that says what it could
    #: not finish (§6.8), never a silently truncated one, so the number is a cost ceiling
    #: rather than a correctness guard — six is two more turns than the longest scripted
    #: investigation needs (list the stops, open one, answer).
    tool_budget: int = 6

    #: §6.8: "after several consecutive failures the run aborts with an honest message."
    #: Consecutive, not total: a model that works around one refused call is working, and
    #: one that cannot get anything back is not. Three is the point at which retrying has
    #: stopped being a retry.
    tool_failure_limit: int = 3

    #: What stage 2 assumes when the question carries no time expression, or one the shift
    #: calendar does not understand (§6.7 row one). It is an expression rather than a
    #: duration because the agent does no date arithmetic: /time/resolve resolves this the
    #: same way it resolves anything the model read out of the question.
    default_time_expression: str = "this shift"

    #: §6.4: the composer may write a summary sentence. Below this many findings it does
    #: not, because a summary of one finding is that finding again.
    summary_after_findings: int = 2

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
