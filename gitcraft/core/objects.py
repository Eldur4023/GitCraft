import hashlib
import json
from datetime import datetime, timezone

from .blocks import BLOCK_SIZE


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()


def make_commit(parent: str | None, tree: dict, message: str = "auto") -> tuple[str, bytes]:
    obj = {
        "message": message,
        "parent": parent,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tree": tree,
        "type": "commit",
    }
    data = _canonical(obj)
    return hashlib.sha256(data).hexdigest(), data


def make_manifest(block_hashes: list[str], file_size: int) -> tuple[str, bytes]:
    obj = {
        "block_size": BLOCK_SIZE,
        "blocks": block_hashes,
        "size": file_size,
        "type": "manifest",
    }
    data = _canonical(obj)
    return hashlib.sha256(data).hexdigest(), data
