"""Constant-time comparison of secrets that may hold any text."""
from __future__ import annotations

import hmac
from typing import Optional


def _as_bytes(value: Optional[str | bytes]) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    # surrogatepass: a lone surrogate (possible in decoded JSON) must not raise either.
    return str(value).encode("utf-8", "surrogatepass")


def same_secret(a: Optional[str | bytes], b: Optional[str | bytes]) -> bool:
    """True when a and b are equal, compared in constant time.

    hmac.compare_digest raises TypeError for a str with non-ASCII characters (a request header or cookie can
    carry them), which would surface as a 500; both sides are compared as UTF-8 bytes instead. None compares as
    the empty value, and an empty value never matches (a missing secret is not a credential)."""
    left, right = _as_bytes(a), _as_bytes(b)
    if not left or not right:
        return False
    return hmac.compare_digest(left, right)
