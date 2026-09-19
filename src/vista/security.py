"""Digests for high-entropy access keys (not human passwords)."""

import hashlib


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
