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
    # extra="ignore" for the reason simulator.config.Settings carries in full: both
    # services read `plant/.env`, and Compose reads it too, so it holds keys that are
    # neither service's -- HOST_UID, HOST_GID, PLANT_HMI_PORT. Worse here than there,
    # because this class declares exactly one field: under the default "forbid" every
    # PLANT_* value the simulator legitimately configures is an extra key to *this*
    # object, so a populated .env aborts the inspection suite during collection even
    # with the uid keys absent.
    model_config = SettingsConfigDict(
        env_prefix="PLANT_", env_file=".env", extra="ignore"
    )

    # Matches simulator.config.Settings.seed's default: SimulatedClassifier must
    # derive from the same configured seed the simulator renders with, or "the same
    # seed" would silently mean two different numbers depending which service you
    # asked (§3.6).
    seed: int = 20260912

    # D7's two rates, configured here because the classifier owns them: §3.5 lists
    # false accepts and false rejects under the line's noise floor, §3.4 assigns them
    # to the vision system, and a real ModelClassifier has them emergently -- so
    # modelling them on the simulator side as well would double-count them the day one
    # drops in.
    #
    # **Starting values chosen by arithmetic against §3.5's 1.5 % defect rate, not
    # measured.** A 33 h history is ~19,800 parts: ~297 genuinely defective, of which
    # ~18 escape at 6 %, and ~78 of the ~19,500 good ones falsely rejected at 0.4 %.
    # That puts roughly one reported reject in five in the false-alarm column -- enough
    # of both errors in one history for M2c's ground truth to score them against,
    # without either swamping the real defect rate. M2c is where they become
    # scoreable; nothing before it reads them.
    false_accept_rate: float = 0.06
    false_reject_rate: float = 0.004

    # **D8's reference**: the RMS contrast of a frame through a clean lens, in grey
    # levels, which `SimulatedClassifier` divides `contrast_of` by to get the clarity it
    # scales every score and the verdict confidence with. §3.5 calls scenario 6 the
    # weakest because the confidence decay is stipulated; this is the number that makes
    # it read off the pixels instead.
    #
    # **Measured, not chosen**, over 200 clean renders at the shipped image settings:
    # mean 66.364 grey levels, sd 0.016 -- a spread of 0.02 %, because the frame is the
    # same geometry every time and only the seeded sensor noise moves. A frame rendered
    # at clarity 0.55 comes back at 0.554 of it, so the statistic tracks the fouling
    # almost exactly. `test_the_configured_reference_contrast_is_what_a_clean_lens_
    # renders` re-measures it, so a change to render.py cannot leave this behind.
    #
    # The cost, stated because it is real and small: a defect big enough to change the
    # frame's own contrast moves this too -- a `missing_part` band renders at 62.6, so
    # such a part reads as 94 % clarity and is reported a few per cent less confidently
    # than a clean one. That is a property of reading confidence off an image, which is
    # what D8 asked for.
    reference_contrast: float = 66.364
