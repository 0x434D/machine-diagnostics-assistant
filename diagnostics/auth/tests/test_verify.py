"""§10.5's validation module: what it accepts, and seven ways of being refused.

Every refusal returns `None` -- the caller answers 401 and says nothing about *which* check
failed, because a 401 that distinguishes "expired" from "wrong audience" is an oracle for
whoever is holding the token. The reason is not lost: it goes to the log, which is where an
operator can read it and an attacker cannot, and that is what these tests assert. A module
whose seven refusals are indistinguishable *from the inside* is a module nobody can debug.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from auth.config import Settings
from auth.testing import AUDIENCE, ISSUER, OTHER_PRIVATE_PEM, PUBLIC_PEM, mint
from auth.tokens import verify


@pytest.fixture
def settings() -> Settings:
    """The deployment's configuration, with the skew at its default of zero."""
    return Settings(public_key=PUBLIC_PEM, audience=AUDIENCE, issuer=ISSUER)


@pytest.fixture(autouse=True)
def _capture(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="auth.tokens")


def test_a_valid_token_yields_its_subject_and_role(settings: Settings) -> None:
    principal = verify(mint(subject="operator-7", role="admin"), settings)

    assert principal is not None
    assert principal.subject == "operator-7"
    assert principal.role == "admin"


def test_no_token_is_refused(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    assert verify(None, settings) is None
    assert verify("", settings) is None
    assert "no token" in caplog.text


def test_a_malformed_token_is_refused(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """Not a JWT at all. The library raises on it; that raise is a known condition with a
    known answer, which is why catching it is a branch rather than a swallowed error."""
    assert verify("not-a-token", settings) is None
    assert "DecodeError" in caplog.text


def test_a_good_signature_for_the_wrong_audience_is_refused(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """§10.5 names the audience check explicitly, and this is the refusal that stops a
    token minted for another service in the same estate from opening this one."""
    assert verify(mint(audience="some-other-service"), settings) is None
    assert "InvalidAudienceError" in caplog.text


def test_a_token_from_another_issuer_is_refused(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    assert verify(mint(issuer="https://issuer.test/somewhere-else"), settings) is None
    assert "InvalidIssuerError" in caplog.text


def test_an_expired_token_is_refused(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    expired = mint(now=datetime.now(UTC) - timedelta(hours=2))

    assert verify(expired, settings) is None
    assert "ExpiredSignatureError" in caplog.text


def test_a_token_signed_by_a_key_we_do_not_trust_is_refused(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """Well-formed, unexpired, right audience, right issuer -- and signed by a keypair whose
    public half this deployment was never given."""
    assert verify(mint(key=OTHER_PRIVATE_PEM), settings) is None
    assert "InvalidSignatureError" in caplog.text


def test_a_token_with_no_role_claim_is_refused(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """Not defaulted to `user`. §10.5 says roles live in the issuer, so a token that carries
    none is a token from an issuer that was not configured to state one -- and inventing the
    lower role here would make that misconfiguration invisible."""
    assert verify(mint(role=None), settings) is None
    assert "no role claim" in caplog.text


def test_a_role_outside_the_two_is_refused(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """§10.5 fixes the set at `admin` and `user`. A third value is not a third role, it is a
    claim this system has no rule for."""
    assert verify(mint(role="superuser"), settings) is None
    assert "superuser" in caplog.text


def test_a_deployment_with_no_key_configured_refuses_everything(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The resting state, and it fails closed. A service that was never given a key can
    verify nothing; the alternative -- letting an unconfigured deployment through -- is how
    the one claim this milestone makes would be false everywhere it was not set up."""
    assert verify(mint(), Settings(audience=AUDIENCE, issuer=ISSUER)) is None
    assert "no public key" in caplog.text


# --- §10.3: the skew is a number, and the number is configuration -------------------------


def _one_second_past_expiry() -> str:
    return mint(now=datetime.now(UTC) - timedelta(minutes=15, seconds=1))


def test_a_token_one_second_past_expiry_is_refused_at_the_default_skew(
    settings: Settings,
) -> None:
    assert settings.clock_skew_seconds == 0.0
    assert verify(_one_second_past_expiry(), settings) is None


def test_the_same_token_is_accepted_inside_a_configured_skew() -> None:
    generous = Settings(
        public_key=PUBLIC_PEM,
        audience=AUDIENCE,
        issuer=ISSUER,
        clock_skew_seconds=30.0,
    )

    assert verify(_one_second_past_expiry(), generous) is not None
