"""Every number in the plant is configuration, never a constant buried in code (§10.3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class ClockConfig:
    history_depth: timedelta
    catchup_speed: float

    # §3.2 assumed the worst boot time was an evening one (~24-25 h back to the last
    # completed night shift). The true supremum is a boot approaching 06:00 itself:
    # the last *completed* night shift is then the one before that -- the ~24 h day
    # gap back to it, plus that night's own length, which peaks at 9 h instead of the
    # usual 8 on the autumn DST Sunday (2026-10-25). 24 h + 9 h = 33 h, approached but
    # never quite reached as boot -> 06:00 that day. Confirmed by
    # test_the_default_depth_covers_every_boot_time_in_the_year, which probes each
    # day's 06:00 boundary rather than an hourly grid (a grid samples every day's
    # interior and misses the true max, which sits at the reset point).
    #
    # This is a plain class constant, not a dataclass field: no type annotation, so it
    # does not become a constructor parameter (see Settings.history_depth_hours below,
    # which derives its default from this one instead of restating the number).
    DEFAULT_HISTORY_DEPTH = timedelta(hours=33)


# pydantic's own metaclass takes `**kwargs: Any` in its __new__, which mypy's strict
# disallow_any_explicit attributes to the *subclass* statement below -- reproduces on
# `class Foo(BaseModel): pass` with no fields of ours at all, so there is no code-level
# fix here. Left as a single, named exception rather than a config-wide disable because
# this is the first pydantic model in the project; if disallow_any_explicit needs the
# same exception at every future Pydantic row model (docs/ENGINEERING.md's plan for the
# analysis service), move it to a per-module mypy.ini override instead of repeating this.
class Settings(BaseSettings):  # type: ignore[explicit-any]
    model_config = SettingsConfigDict(env_prefix="PLANT_", env_file=".env")

    # clock — default derives from ClockConfig.DEFAULT_HISTORY_DEPTH so the two
    # spellings of the same number (a timedelta here, hours as a float for env-var
    # ergonomics) cannot drift apart; main() converts this back to a timedelta
    # when it builds a ClockConfig.
    history_depth_hours: float = ClockConfig.DEFAULT_HISTORY_DEPTH / timedelta(hours=1)
    # 700x keeps catch-up to ~170 s wall (33 h / 699) for the 33 h depth default,
    # inside R1's <=180 s budget with the depth floor intact; 600x would need ~198 s
    # for the same depth. Task 10 measures the real catchup_wall; Task 17 records
    # what was measured, same as the depth default above.
    catchup_speed: float = 700.0

    # line
    takt_seconds: float = 6.0
    seed: int = 20260912
    # Additive Gaussian jitter on the takt written to TaktTime, ~0.8 % of a 6 s takt.
    # Not §3.5's noise model (that arrives in M2) -- this exists so TaktTime is
    # historised at all: asyncua's monitored-item filter drops a notification
    # whenever the written value is unchanged, and a bare constant takt (M1, with no
    # noise model yet) means only the very first write is ever historised. See
    # station_s3._next_takt.
    takt_jitter_sigma: float = 0.05

    # catch-up pacing -- asyncua's own per-monitored-item notification queue caps at
    # 10,000 and silently discards the oldest entry past that, so generate_history
    # must give the ~10 ms publish loop a chance to drain before any one stream's
    # backlog gets there. 500 parts is comfortably under the cap even though every
    # part now writes a distinct TaktTime (see takt_jitter_sigma); 0.05 s matches
    # what was measured to drain a batch that size. See
    # station_s3.generate_history.
    catchup_batch_size: int = 500
    catchup_batch_pause_seconds: float = 0.05

    # inspection — M1's reject rate is a measurement knob for R4, not §3.5's
    # 1.5 % noise floor, which arrives with the noise model in M2.
    reject_rate: float = 0.05
    image_width: int = 640
    image_height: int = 480
    model_version: str = "simulated-1"

    # boundary
    endpoint_url: str = "opc.tcp://line-simulator:4840/plant"
    application_uri: str = "urn:machine-agent:plant:line-simulator"
    pki_root: str = "/pki"
    history_page_size: int = 1000
    inspection_url: str = "http://inspection-service:8100"
