import hashlib
import hmac
from pathlib import Path


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_hmac(secret: bytes, namespace: str, value: str) -> str:
    if not secret:
        raise ValueError("fingerprint secret must not be empty")
    payload = f"{namespace}\x1f{value}".encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()
