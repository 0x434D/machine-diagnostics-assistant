"""Every number and connection string is configuration (§10.3)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANALYSIS_")

    database_url: str = "postgresql://postgres@localhost:5432/postgres"

    # §5.3 returns a handful of reject serials so the agent has something concrete to cite
    # rather than a bare count.
    sample_serial_limit: int = 5
