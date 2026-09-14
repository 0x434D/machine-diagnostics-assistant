"""Every number and connection string is configuration (§10.3)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANALYSIS_")

    database_url: str = "postgresql://postgres@localhost:5432/postgres"

    # §5.3 returns a handful of reject serials so the agent has something concrete to cite
    # rather than a bare count.
    sample_serial_limit: int = 5

    # At or above which per-class score /inspection/stats counts a class as seen (§10.3:
    # every number is configuration). §3.4's six scores are independent and do not sum to
    # 1, so there is no "the class" to take without one — and the number belongs here
    # rather than in the SQL, because the right value is a property of whichever classifier
    # is deployed and not of this query. `inspection.classifier` is the one deployed today
    # and separates its two populations widely: a class the model believes it saw is drawn
    # from [0.55, 0.95) and every other class from [0.01, 0.08).
    defect_class_threshold: float = 0.5
