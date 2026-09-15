"""Every number and connection string is configuration (§10.3)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANALYSIS_")

    # The `analysis` role and the diagnostics database, not `postgres` and `postgres` — which
    # is what this said until M3 and is the one place the grant boundary could still be
    # absent. A developer who runs the service with no environment set gets the role the
    # deployment uses, so a query written by hand here meets the same refusals it will meet
    # in Compose. As superuser every such query succeeded, which is exactly backwards: the
    # boundary was missing where someone is most likely to write something new against it.
    #
    # No password, so a local run that has not been given one fails to authenticate rather
    # than quietly reaching a database with different rights.
    database_url: str = "postgresql://analysis@localhost:5432/diagnostics"

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
