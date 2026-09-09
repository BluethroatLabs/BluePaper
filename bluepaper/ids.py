from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ID_PREFIX = "cnv_"


def new_conversion_id() -> str:
    """Return an opaque conversion id: cnv_ + Crockford ULID."""
    return _ID_PREFIX + _ulid()


def is_conversion_id(value: str) -> bool:
    if not value.startswith(_ID_PREFIX):
        return False
    body = value[len(_ID_PREFIX) :]
    if len(body) != 26:
        return False
    return all(ch in _CROCKFORD for ch in body.upper())


def _ulid() -> str:
    ts = int(time.time() * 1000)
    if ts < 0 or ts >= 2**48:
        raise ValueError("timestamp out of ULID range")
    body = ts.to_bytes(6, "big") + os.urandom(10)
    n = int.from_bytes(body, "big")
    chars = ["0"] * 26
    for i in range(25, -1, -1):
        chars[i] = _CROCKFORD[n & 31]
        n >>= 5
    return "".join(chars)
