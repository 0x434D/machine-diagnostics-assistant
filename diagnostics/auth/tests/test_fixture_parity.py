"""This side of the two-implementation agreement, over `fixtures/tokens.json`.

The edge gateway is C# and cannot import this module (§2.3), so §10.5's rule is written
twice. Two implementations of one rule drift, and they drift quietly -- the second one keeps
accepting what it always accepted while the first learns a check it never gained. What stops
that is not a second reading of the rule, it is a shared set of *bytes*: this suite and
`Gateway.Tests/TokenFixtureParityTests.cs` read the same twelve tokens out of the same file
and must reach the same verdict on every one.

The settings are asserted too, and that is not ceremony. A gateway that checked a different
audience, or tolerated five minutes of clock skew where these services tolerate none, would
agree on all twelve verdicts below and still not be running the same rule.

Regenerate with `cd diagnostics && uv run --frozen python ../scripts/mint-fixtures.py`,
which mints from a keypair it never writes to disk.
"""

from __future__ import annotations

import json
import pathlib
from typing import Final

import pytest
from auth.config import Settings
from auth.testing import mint
from auth.tokens import verify

_RAW = json.loads(
    (pathlib.Path(__file__).resolve().parents[1] / "fixtures/tokens.json").read_text()
)

PUBLIC_KEY: Final[str] = _RAW["public_key"]
SETTINGS: Final[dict[str, object]] = _RAW["settings"]
TOKENS: Final[list[dict[str, object]]] = _RAW["tokens"]


def _text(fixture: dict[str, object], key: str) -> str:
    value = fixture[key]
    assert isinstance(value, str), f"fixture field {key!r} is {value!r}, not a string"
    return value


@pytest.fixture
def settings() -> Settings:
    """The deployment the fixtures were minted for: this file's key, everything else the
    module's own default, so a default that moved fails here rather than being papered over
    by a fixture that moved with it."""
    return Settings(public_key=PUBLIC_KEY)


def test_the_fixtures_were_minted_for_this_deployment() -> None:
    defaults = Settings()

    assert SETTINGS == {
        "audience": defaults.audience,
        "issuer": defaults.issuer,
        "algorithm": defaults.algorithm,
        "role_claim": defaults.role_claim,
        "clock_skew_seconds": defaults.clock_skew_seconds,
    }


def test_every_case_the_plan_names_is_present() -> None:
    """The fixture file is the shared half of a two-implementation agreement, so a case
    silently dropped from it weakens both suites at once and neither would fail."""
    assert {_text(fixture, "name") for fixture in TOKENS} >= {
        "valid_user",
        "expired",
        "wrong_audience",
        "untrusted_key",
        "no_role_claim",
        "role_outside_the_two",
    }


@pytest.mark.parametrize(
    "fixture", TOKENS, ids=[_text(fixture, "name") for fixture in TOKENS]
)
def test_the_verdict_matches_the_fixture(
    fixture: dict[str, object], settings: Settings
) -> None:
    principal = verify(_text(fixture, "token"), settings)

    if fixture["accepted"]:
        assert principal is not None, _text(fixture, "why")
        assert principal.subject == _text(fixture, "subject")
        assert principal.role == _text(fixture, "role")
    else:
        assert principal is None, _text(fixture, "why")


def test_the_fixture_key_is_the_only_one_these_fixtures_verify_against() -> None:
    """The suite above would pass just as well against a validator that accepted anything
    signed by anyone. This is the fixture file's own negative: a token minted here, correct
    in every claim, is refused by the settings the fixtures configure."""
    assert verify(mint(), Settings(public_key=PUBLIC_KEY)) is None
