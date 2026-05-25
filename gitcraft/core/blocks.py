import hashlib
from pathlib import Path

BLOCK_SIZE = 4096  # 4 KB


def hash_file(path: Path) -> str:
    """Return the SHA-256 hex digest of *path*.

    Raises OSError (including FileNotFoundError / PermissionError) if the
    file cannot be read — callers are expected to handle this.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def split_blocks(data: bytes) -> list[tuple[str, bytes]]:
    """Split data into BLOCK_SIZE chunks. Returns [(sha256, chunk), ...]."""
    if not data:
        return []
    result = []
    for i in range(0, len(data), BLOCK_SIZE):
        block = data[i : i + BLOCK_SIZE]
        result.append((hashlib.sha256(block).hexdigest(), block))
    return result
