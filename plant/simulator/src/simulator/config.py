"""Every number in the plant is configuration, never a constant buried in code (§10.3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class ClockConfig:
    history_depth: timedelta
    catchup_speed: float
    live_speed: float = 1.0

    # §3.2 assumed the worst boot time was an evening one (~24-25 h back to the last
    # completed night shift). Measured worst case is a boot between 00:00 and 06:00,
    # inside a night shift that has not yet reached its own 06:00 end: the last
    # *completed* night shift is then the one before that, ~31 h back on an ordinary
    # day, 32 h when that span crosses the autumn DST fall-back (2026-10-25 05:00
    # Europe/Berlin is the worst hour in 2026). Confirmed by
    # test_the_default_depth_covers_every_boot_time_in_the_year.
    #
    # This is a plain class constant, not a dataclass field: no type annotation, so it
    # does not become a constructor parameter (see Settings.history_depth_hours below,
    # which derives its default from this one instead of restating the number).
    DEFAULT_HISTORY_DEPTH = timedelta(hours=32)


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
    catchup_speed: float = 600.0

    # line
    takt_seconds: float = 6.0
    seed: int = 20260912

    # inspection — M1's reject rate is a measurement knob for R4, not §3.5's
    # 1.5 % noise floor, which arrives with the noise model in M2.
    reject_rate: float = 0.05
    image_width: int = 640
    image_height: int = 480

    # boundary
    endpoint_url: str = "opc.tcp://line-simulator:4840/plant"
    application_uri: str = "urn:machine-agent:plant:line-simulator"
    pki_root: str = "/pki"
    history_page_size: int = 1000
    inspection_url: str = "http://inspection-service:8100"
