import hashlib
from pathlib import Path

BLOCK_SIZE = 4096


def hash_file(path: Path) -> str:
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
