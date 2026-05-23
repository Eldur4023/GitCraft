import tomllib
from pathlib import Path


def load_config(world_dir: Path) -> dict:
    cfg_path = world_dir / ".gitcraft" / "config.toml"
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"No GitCraft config at {cfg_path}. Run 'gitcraft init' first."
        )
    with open(cfg_path, "rb") as f:
        return tomllib.load(f)
