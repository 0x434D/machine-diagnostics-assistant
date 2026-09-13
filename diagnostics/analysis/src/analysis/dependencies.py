"""Settings as a FastAPI dependency, so a test can override the database it points at."""

from __future__ import annotations

from analysis.config import Settings


def settings_dependency() -> Settings:
    return Settings()
