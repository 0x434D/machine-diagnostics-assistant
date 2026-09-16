"""CLAUDE.md's fifth invariant, as an assertion rather than prose.

§10.5: "the plant stack authenticates separately and must never share an issuer with the
diagnostics stack: a shared token issuer or user store would be a second channel across
the boundary, which is exactly what the two-stack split exists to prevent." The plant's
half of that is one shared secret, §3.7's fault-injection token; the diagnostics half is
the JWT validation module (`diagnostics/auth`, M5's Task 1). This test reads both
stacks' own configuration -- never a list of names copied here and left to rot -- and
asserts they hold nothing in common: not a variable name, not a value.

`test_compose_invariants.py` proved the network and volume half of the two-stack split
is real rather than assumed; this is the identity half, read the same way -- from the
files each stack actually loads, parsed rather than grepped.
"""

from __future__ import annotations

from pathlib import Path

from _compose_yaml import load as _load
from _compose_yaml import services as _services

REPO = Path(__file__).resolve().parents[3]
PLANT_ENV_EXAMPLE = REPO / "plant" / ".env.example"
DIAGNOSTICS_ROOT = REPO / "diagnostics"
DIAGNOSTICS_COMPOSE = DIAGNOSTICS_ROOT / "compose.yml"

FAULT_TOKEN_VAR = "PLANT_FAULT_INJECTION_TOKEN"
"""The plant's own name for §10.5's shared secret, held as one literal so a rename on
the plant side is exactly the change that makes this test stop finding it -- see the
assertion that it is still in `plant/.env.example` below."""


def _env_pairs(path: Path) -> dict[str, str]:
    """`KEY=value` pairs from a `.env`-shaped file, ignoring blank lines and comments.

    Not a dotenv library: every consumer of this format in the repository so far is
    Compose's own interpolation and pydantic-settings' own reader, and a dependency
    pulled in to parse four kinds of line here would need the justification §10.7 asks
    of a new import for a job this does without one.
    """
    pairs: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        pairs[key.strip()] = value.strip()
    return pairs


def _environment_pairs(environment: object) -> dict[str, str]:
    """`environment:`'s two accepted shapes, normalised to one dict.

    Compose accepts a mapping or a list of `KEY=VALUE` strings (or a bare `KEY`, which
    passes the host's own value through and carries no literal here to compare) -- the
    same ambivalence `test_compose_invariants.py`'s `_networks_of` normalises for
    `networks:`, for a different field with different value semantics.
    """
    if isinstance(environment, dict):
        return {str(key): str(value) for key, value in environment.items()}
    pairs: dict[str, str] = {}
    if isinstance(environment, list):
        for entry in environment:
            name, _, value = str(entry).partition("=")
            pairs[name] = value
    return pairs


def _diagnostics_configuration() -> tuple[set[str], set[str]]:
    """Every environment variable name, and every non-empty literal value, the
    diagnostics stack configures anywhere -- its Compose file and every `.env.example`
    under it.

    Read fresh from those files rather than a vocabulary maintained here, for the same
    reason `test_compose_invariants.py` parses YAML instead of matching strings: a name
    typed once and never revisited is a check that has stopped checking anything. That
    also means this stays correct as M5's `diagnostics/auth` lands beside this change --
    whatever settings that module adds to `diagnostics/compose.yml` or to a
    `diagnostics/.env.example` are read the day this runs, not the day this was written.
    """
    names: set[str] = set()
    values: set[str] = set()

    if DIAGNOSTICS_COMPOSE.exists():
        for service in _services(_load(DIAGNOSTICS_COMPOSE)).values():
            for name, value in _environment_pairs(
                service.get("environment") or {}
            ).items():
                names.add(name)
                # `${VAR:-default}` is Compose's own interpolation syntax, not a literal
                # this service holds -- collecting the whole string would flag every
                # service that happens to interpolate the same host default (HOST_UID
                # and its like), which is noise this test has no business raising.
                if value and "${" not in value:
                    values.add(value)

    for env_example in DIAGNOSTICS_ROOT.rglob(".env.example"):
        for name, value in _env_pairs(env_example).items():
            names.add(name)
            if value:
                values.add(value)

    return names, values


def test_the_plants_fault_injection_token_shares_no_name_or_value_with_diagnostics() -> (
    None
):
    """§10.5's line, checked rather than trusted: a shared token issuer or user store
    would be a second channel across the two-stack boundary, so the plant's one shared
    secret has to be a name the diagnostics stack has never used, at a value it could
    never produce by coincidence.

    Checked against `plant/.env.example` rather than `Settings().fault_injection_token`
    (which defaults to `None`): a `None` can never collide with anything, and the
    interesting case is the name a real deployment sets it under, and -- once
    `diagnostics/auth` ships its own settings -- the values that name's counterparts
    there are allowed to take.

    **How to see this go red**, because a check that only ever passes proves nothing:
    rename `FAULT_TOKEN_VAR` in this file and in `plant/.env.example` to a name
    `diagnostics/compose.yml` already configures (`POSTGRES_PASSWORD`, say), or set
    `plant/.env.example`'s placeholder to a value diagnostics configures somewhere, and
    the assertion below names exactly what collided. Both were tried by hand while
    writing this test and both failed as described; neither is left in place, because a
    repository that shipped one on purpose would be demonstrating the failure this test
    exists to catch.
    """
    plant_env = _env_pairs(PLANT_ENV_EXAMPLE)
    assert FAULT_TOKEN_VAR in plant_env, (
        f"{FAULT_TOKEN_VAR} left plant/.env.example; this test now proves nothing "
        "about it"
    )

    diagnostics_names, diagnostics_values = _diagnostics_configuration()

    assert FAULT_TOKEN_VAR not in diagnostics_names, (
        f"{FAULT_TOKEN_VAR} is also a name the diagnostics stack configures -- one "
        "variable feeding both identity mechanisms is exactly the shared channel "
        "§10.5 forbids"
    )

    token_value = plant_env[FAULT_TOKEN_VAR]
    assert not token_value or token_value not in diagnostics_values, (
        f"plant/.env.example's {FAULT_TOKEN_VAR} matches a value the diagnostics stack "
        "configures; the two identity mechanisms must not agree on so much as a string"
    )
