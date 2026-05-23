import json
from pathlib import Path

_REGISTRY = Path.home() / ".gitcraft" / "worlds.json"


def load() -> dict[str, str]:
    if not _REGISTRY.exists():
        return {}
    return json.loads(_REGISTRY.read_text(encoding="utf-8"))


def save(registry: dict[str, str]) -> None:
    _REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY.write_text(json.dumps(registry, indent=2), encoding="utf-8")


def register(name: str, path: Path) -> None:
    r = load()
    r[name] = str(path.resolve())
    save(r)


def resolve(name: str) -> Path:
    r = load()
    if name not in r:
        raise KeyError(name)
    return Path(r[name])
