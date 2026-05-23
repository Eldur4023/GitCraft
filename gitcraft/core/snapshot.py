from pathlib import Path

_EXCLUDE = {".gitcraft", "session.lock"}


def scan(world_dir: Path) -> dict[str, Path]:
    """Return {relative_posix_path: absolute_path} for all world files."""
    result: dict[str, Path] = {}
    for p in world_dir.rglob("*"):
        if p.is_dir():
            continue
        if any(part in _EXCLUDE for part in p.relative_to(world_dir).parts):
            continue
        result[p.relative_to(world_dir).as_posix()] = p
    return result
