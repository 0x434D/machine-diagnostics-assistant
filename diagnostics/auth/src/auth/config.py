"""Every value the token rules rest on is configuration (§10.3), and no key lives here."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTH_")

    # The issuer's public key, PEM-encoded. Empty by default and fails closed: a service
    # that was never given one verifies nothing and refuses everything, which is the only
    # resting state that does not make §1.8's claim false wherever nobody set it up.
    #
    # A key rather than a JWKS URL because there is no issuer to fetch a key set *from*:
    # M5 is deliberately slim and the development issuer is a script with a keypair on disk
    # (scripts/mint-token.py). When Zitadel arrives, this is where its JWKS URL goes and
    # `tokens.verify` gains one line -- the rest of the module does not change, which is the
    # property that made brokering worth choosing in §10.5.
    public_key: str = ""

    # §10.5's audience check, named as one of the three things a service does with a token.
    # A token minted for another service in the same estate must not open this one.
    audience: str = "machine-agent"

    # The one issuer this application is a client of. §10.5 is emphatic that there is
    # exactly one; validating several directly is the alternative it rules out.
    issuer: str = "https://issuer.test/machine-agent"

    # RS256 rather than a list: an `algorithms` list of one is what stops `alg: none` and
    # the HMAC-with-the-public-key confusion, and a deployment that needs a second one has
    # changed issuers rather than gained a setting.
    algorithm: str = "RS256"

    # Which claim carries §10.5's role. Configuration because it is the one part of the
    # claim shape an issuer decides for us -- Zitadel's default is a nested object under a
    # namespaced key, and moving it must not be a code change.
    role_claim: str = "role"

    # How much clock difference between the issuer and this service a token's `exp` and
    # `iat` are given, in seconds.
    #
    # **Zero, and the default is a statement rather than a placeholder.** Every container in
    # this deployment runs on one host and reads one kernel clock, so there is no skew to
    # tolerate -- and any non-zero default silently extends the life of every token ever
    # issued by that much. The number exists because the issuer will one day be somewhere
    # else, and moving it then is configuration rather than a release.
    clock_skew_seconds: float = 0.0
