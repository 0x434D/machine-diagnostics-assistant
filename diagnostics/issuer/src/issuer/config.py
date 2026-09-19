"""Who may sign in, with what role, for how long, and with which key (§10.3).

Every one of those is configuration and none of it is in this file. There is no user store
and no registration (§10.5 is explicit that both are deliberately not built), so "the users"
is a setting an operator writes and a deployment reads -- which is also what makes this
issuer replaceable by a real one without a code change.

The claim *shape* is not here either: the audience, the issuer name, the algorithm and the
role claim are read from `auth.config.Settings`, the same AUTH_* variables the four
validators read. A second statement of them here is the drift that would mint tokens every
service in the stack refuses, for a reason nobody would look for.
"""

from __future__ import annotations

from auth.tokens import ROLES
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class User(BaseModel):
    """One account, as configuration states it."""

    password: str
    role: str

    @field_validator("role")
    @classmethod
    def _known_role(cls, role: str) -> str:
        """§10.5's two, closed. Refused here rather than at the token endpoint, so a
        deployment configured with a third role fails to start instead of issuing tokens
        that every service refuses at a point far from the typo."""
        if role not in ROLES:
            raise ValueError(f"role {role!r} is not one of {sorted(ROLES)}")
        return role


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ISSUER_")

    # The signing key, PEM-encoded, private half. Empty by default and fails closed: an
    # issuer that was never given a key mints nothing and serves an empty key set, which is
    # the only resting state that cannot hand out a token no service will honour.
    #
    # Through the environment rather than the `secrets:` block docs/ENGINEERING.md §7
    # prefers, and the exception is deliberate: this key's *public* half already travels
    # that way as AUTH_PUBLIC_KEY, the pair is a gitignored development keypair, and a
    # `file:`-backed secret makes `docker compose up` fail before the issuer exists to say
    # what is missing. When there is a real key to protect there is a real issuer holding
    # it, and this setting is gone with the service.
    private_key: str = ""

    # The accounts, as `{"name": {"password": "...", "role": "user"}}`. Empty by default,
    # which refuses every credential -- an issuer that invented a default account would be
    # a credential in the repository with extra steps.
    users: dict[str, User] = {}

    # How long a minted token is good for. §10.3, and it is the number this issuer exists to
    # let somebody turn: short enough that a leaked token expires, long enough that a
    # question and its follow-ups fit inside one.
    token_lifetime_minutes: float = 60.0
