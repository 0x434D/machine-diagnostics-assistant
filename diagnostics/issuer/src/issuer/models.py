"""What crosses the wire at this issuer's two endpoints.

Separate from `app.py` so that the module holding the credential comparison keeps
`disallow_any_explicit` at full strength: pydantic's metaclass takes `**kwargs: Any` in its
`__new__`, which mypy attributes to every `BaseModel` subclass statement, and the override
that silences it is scoped to modules that declare models and nothing else (mypy.ini, and
the same shape `analysis.models` already takes). An `Any` on the path a password takes is
the one this project can least afford to wave through.
"""

from __future__ import annotations

from pydantic import BaseModel


class Credentials(BaseModel):
    """A username and a password, and deliberately nothing else -- a role in the request
    body is the obvious escalation, and the way to close it is not to have the field."""

    username: str
    password: str


class Token(BaseModel):
    """RFC 6749's shape, so that replacing this issuer with a real one is a configuration
    change on the client rather than a second response format to parse."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int


class KeySet(BaseModel):
    """RFC 7517's JWK Set. One key today; a list because rotation is what the shape is
    for, and a verifier that fetched a bare key could not follow one."""

    keys: list[dict[str, str]]
