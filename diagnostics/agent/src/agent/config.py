"""Every setting is configuration (§10.3), and no credential lives here."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_")

    analysis_url: str = "http://analysis:8000"

    #: "scripted" or "anthropic". Scripted is the default because it needs no credentials
    #: and reaches no network; anthropic reads ANTHROPIC_API_KEY from the environment.
    provider: str = "scripted"

    #: §6.2's retrieval budget in its M1 form: the tool loop is bounded so a provider that
    #: keeps asking cannot run forever.
    tool_budget: int = 4
