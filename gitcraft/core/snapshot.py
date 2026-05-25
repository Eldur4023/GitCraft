import sys
from pathlib import Path

_EXCLUDE = {".gitcraft", "session.lock"}


def _warn(msg: str) -> None:
    try:
        import click
        click.echo(f"⚠  {msg}", err=True)
    except ImportError:
        print(f"WARNING: {msg}", file=sys.stderr)


def scan(world_dir: Path) -> dict[str, Path]:
    """Return {relative_posix_path: absolute_path} for all world files."""
    result: dict[str, Path] = {}
    for p in world_dir.rglob("*"):
        try:
            if p.is_dir():
                continue
            if any(part in _EXCLUDE for part in p.relative_to(world_dir).parts):
                continue
            result[p.relative_to(world_dir).as_posix()] = p
        except (OSError, ValueError) as exc:
            _warn(f"Skipping {p}: {exc}")
    return result
