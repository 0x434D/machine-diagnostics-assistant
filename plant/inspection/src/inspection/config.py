"""The inspection service's own configuration.

A separate uv workspace member from `simulator`, so this does not import
`simulator.config.Settings` (§10.7); the one field the two must agree on (`seed`) is
duplicated deliberately, the same way `render.py` is.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


# pydantic's own metaclass takes `**kwargs: Any` in its __new__, which mypy's strict
# disallow_any_explicit attributes to the *subclass* statement below, not to any
# annotation of ours -- the identical false positive `simulator.config.Settings`
# already carries this exact suppression for. One occurrence in this file, so the
# inline ignore is the right size here, not a scoped mypy.ini override (that is
# reserved for inspection.schemas, which has three).
class Settings(BaseSettings):  # type: ignore[explicit-any]
    model_config = SettingsConfigDict(env_prefix="PLANT_", env_file=".env")

    # Matches simulator.config.Settings.seed's default: SimulatedClassifier must
    # derive from the same configured seed the simulator renders with, or "the same
    # seed" would silently mean two different numbers depending which service you
    # asked (§3.6).
    seed: int = 20260912
