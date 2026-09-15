"""Every number and connection string is configuration (§10.3)."""

from __future__ import annotations

from datetime import timedelta

from pydantic_settings import BaseSettings, SettingsConfigDict

from analysis.propagation import DEFAULT_LEAD_IN
from analysis.significance import DEFAULT_SIGNIFICANCE
from analysis.stops import DEFAULT_INTERRUPTION_FLOOR, DEFAULT_MICRO_STOP_THRESHOLD


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

    # --- the numbers the pure modules already carry, restated as the deployment's ---------
    #
    # Each default is read off the module that defines it rather than typed again, so there
    # is one number and an environment variable that moves it. A second literal here would
    # be a copy that agrees today and drifts the first time either side is retuned — and the
    # copy nobody notices is the one in the file that is not being edited.
    micro_stop_threshold_seconds: float = DEFAULT_MICRO_STOP_THRESHOLD.total_seconds()
    interruption_floor_seconds: float = DEFAULT_INTERRUPTION_FLOOR.total_seconds()
    propagation_lead_in_seconds: float = DEFAULT_LEAD_IN.total_seconds()

    # How far before a stop the query layer reads state and buffer history.
    #
    # **Not the same number as the lead-in, and it must not become one.** The lead-in is how
    # far back of *one* buffer condition the walk may look; the chain applies it again at
    # every link, so a six-link chain reaches six lead-ins back. A query that fetched only
    # `lead_in` of history would hand the walk a timeline that stops mid-chain, and the walk
    # would terminate `UNEXPLAINED` for want of rows that exist — a chain that looks
    # legitimately unexplainable is the worst shape this service can return, because nothing
    # in the answer says the evidence was withheld from it.
    propagation_history_seconds: float = 6 * DEFAULT_LEAD_IN.total_seconds()

    # Ceilings on what a single response carries. Counts stay exact; it is the row lists
    # that are capped, and every capped list is returned beside the flag that says so.
    stop_limit: int = 200
    signal_point_limit: int = 5_000
    affected_serial_limit: int = 200

    # The bucket `/inspection/stats?group_by=time` and the time-bucket pattern dimension
    # both cut the window into. One number, so a share reported by one is a share of the
    # same interval as a share reported by the other.
    stats_time_bucket_minutes: int = 60

    # §5.5's two numbers, defaulted from `significance.SignificanceSettings` for the reason
    # given above. The gate is a floor on meaning: below it the honest answer is that we
    # could not look, which is not the same answer as nothing being there.
    significance_alpha: float = DEFAULT_SIGNIFICANCE.alpha
    significance_minimum_sample: int = DEFAULT_SIGNIFICANCE.minimum_sample

    # Within how much of `now` the newest row must lie for `/line/status` to call itself
    # live. Ten takts at §3.1's 6 s: long enough that an ordinary micro-stop does not read
    # as an outage, short enough that a stopped gateway does. The number is reported beside
    # the verdict rather than left here alone, because a boolean whose threshold the reader
    # cannot see is a boolean the agent would quote as if it meant something else.
    line_status_live_within_seconds: float = 60.0

    @property
    def micro_stop_threshold(self) -> timedelta:
        return timedelta(seconds=self.micro_stop_threshold_seconds)

    @property
    def interruption_floor(self) -> timedelta:
        return timedelta(seconds=self.interruption_floor_seconds)

    @property
    def propagation_lead_in(self) -> timedelta:
        return timedelta(seconds=self.propagation_lead_in_seconds)

    @property
    def propagation_history(self) -> timedelta:
        return timedelta(seconds=self.propagation_history_seconds)

    @property
    def stats_time_bucket(self) -> timedelta:
        return timedelta(minutes=self.stats_time_bucket_minutes)
