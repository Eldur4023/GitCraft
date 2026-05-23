import json
from pathlib import Path

_INDEX = Path(".gitcraft") / "index.json"
_OBJECT_CACHE = Path(".gitcraft") / "object-cache"


def load_index(world_dir: Path) -> dict:
    path = world_dir / _INDEX
    if not path.exists():
        return {"head": None, "tree": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_index(world_dir: Path, index: dict) -> None:
    path = world_dir / _INDEX
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2), encoding="utf-8")


def load_known_objects(world_dir: Path) -> set[str]:
    path = world_dir / _OBJECT_CACHE
    if not path.exists():
        return set()
    return set(path.read_text(encoding="utf-8").splitlines())


def add_known_objects(world_dir: Path, hashes: set[str]) -> None:
    path = world_dir / _OBJECT_CACHE
    existing = load_known_objects(world_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sorted(existing | hashes)), encoding="utf-8")
