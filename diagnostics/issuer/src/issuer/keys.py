"""The signing key, and the same key as the document a verifier would fetch.

**The JWKS document is derived from the private key, never configured beside it.** A key set
that is its own setting is a key set that can disagree with what actually signed -- and a
verifier fetching it would then refuse every token for a reason neither side can see. Here
the two cannot come apart: the public half is computed from the half that signs.
"""

from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from typing import Final

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

KEY_TYPE: Final = "RSA"
"""`auth.config.Settings.algorithm` is RS256 and is one value rather than a list, so the
configured key is an RSA key or this issuer cannot sign at all."""


@lru_cache(maxsize=1)
def signing_key(pem: str) -> rsa.RSAPrivateKey | None:
    """The configured signing key, or `None` when none is configured.

    Raises `ValueError` on an unreadable PEM and `TypeError` on a key RS256 cannot sign
    with. Both are a deployment error and both propagate: an issuer that swallowed either
    would start, answer, and mint nothing anybody could use.
    """
    if not pem.strip():
        return None
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError(
            f"the configured signing key is {type(key).__name__}; RS256 needs an RSA key"
        )
    return key


def jwk(public_key: rsa.RSAPublicKey, algorithm: str) -> dict[str, str]:
    """One JWK (RFC 7517) for a public key, carrying the `kid` its tokens are headed with.

    The two parameters are base64url-encoded big-endian integers (RFC 7518 §6.3.1), built
    from the key's own numbers rather than taken from a library's JWK export: the same two
    values are the input to the thumbprint below, and reading them out of an untyped export
    to compute it would be a second shape to keep in step for no gain.
    """
    numbers = public_key.public_numbers()
    modulus = _b64u_int(numbers.n)
    exponent = _b64u_int(numbers.e)
    return {
        "kty": KEY_TYPE,
        "n": modulus,
        "e": exponent,
        "alg": algorithm,
        "use": "sig",
        "kid": thumbprint(modulus, exponent),
    }


def thumbprint(modulus: str, exponent: str) -> str:
    """RFC 7638's key thumbprint: the `kid`, computed rather than invented.

    A name somebody chose would have to be configured beside the key and kept in step with
    it. This one is a function of the key, so a rotated key gets a new `kid` for free and a
    token signed by the old one is identifiably old rather than merely refused.
    """
    canonical = json.dumps(
        {"e": exponent, "kty": KEY_TYPE, "n": modulus},
        separators=(",", ":"),
        sort_keys=True,
    )
    return _b64u(hashlib.sha256(canonical.encode()).digest())


def _b64u_int(value: int) -> str:
    return _b64u(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
