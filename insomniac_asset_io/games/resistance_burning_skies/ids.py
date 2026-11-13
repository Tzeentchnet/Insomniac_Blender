from __future__ import annotations

import hashlib


def stable_resource_id(namespace: str, value: str) -> int:
    digest = hashlib.blake2b(
        value.casefold().encode("utf-8"),
        digest_size=8,
        person=namespace.encode("ascii")[:16],
    ).digest()
    value_id = int.from_bytes(digest, "big")
    return value_id or 1
