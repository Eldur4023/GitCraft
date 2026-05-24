import asyncio
import json
from pathlib import Path

import click

from . import registry as reg
from .config import load_config
from .core.blocks import hash_file, split_blocks
from .core.index import add_known_objects, load_index, load_known_objects, save_index
from .core.objects import make_commit, make_manifest
from .core.snapshot import scan
from .transport.sftp import SSHTransport


# ── helpers ──────────────────────────────────────────────────────────────────

def _world_dir(name: str) -> Path:
    try:
        return reg.resolve(name)
    except KeyError:
        raise click.ClickException(
            f"Unknown world '{name}'. Run 'gitcraft init {name} <path>' first."
        )


async def _connect(cfg: dict) -> SSHTransport:
    r = cfg["remote"]
    return await SSHTransport.connect(
        host=r["host"],
        port=r.get("port", 22),
        user=r["user"],
        remote_path=r["path"],
        key_file=r.get("key_file"),
        password=r.get("password"),
    )


async def _push_async(world_dir: Path, cfg: dict, message: str = "auto") -> bool:
    index = load_index(world_dir)
    known = load_known_objects(world_dir)
    files = scan(world_dir)
    hashes = {rel: hash_file(p) for rel, p in files.items()}
    changed = {
        rel: (files[rel], hashes[rel])
        for rel in files
        if index["tree"].get(rel, {}).get("sha256") != hashes[rel]
    }
    deleted = [r for r in index["tree"] if r not in files]

    if not changed and not deleted:
        click.echo("Nothing to push.")
        return False

    new_tree = dict(index["tree"])
    new_known: set[str] = set()
    blocks_to_upload: list[tuple[str, bytes]] = []
    manifests_to_upload: list[tuple[str, bytes]] = []

    for rel, (abs_path, file_hash) in changed.items():
        data = abs_path.read_bytes()
        blocks = split_blocks(data)
        block_hashes = [h for h, _ in blocks]
        manifest_hash, manifest_data = make_manifest(block_hashes, len(data))

        for bh, bdata in blocks:
            if bh not in known:
                blocks_to_upload.append((bh, bdata))
                new_known.add(bh)

        if manifest_hash not in known:
            manifests_to_upload.append((manifest_hash, manifest_data))
            new_known.add(manifest_hash)

        new_tree[rel] = {"sha256": file_hash, "manifest": manifest_hash}

    for rel in deleted:
        del new_tree[rel]

    click.echo(
        f"Uploading {len(blocks_to_upload)} block(s) + "
        f"{len(manifests_to_upload)} manifest(s) via tar..."
    )
    transport = await _connect(cfg)
    try:
        with click.progressbar(length=2, label="Uploading", width=40) as bar:
            await transport.put_blocks(blocks_to_upload)
            bar.update(1)
            await transport.put_manifests(manifests_to_upload)
            bar.update(1)

        commit_hash, commit_data = make_commit(index.get("head"), new_tree, message)
        await transport.put_commit(commit_hash, commit_data)
        new_known.add(commit_hash)
        await transport.set_head(commit_hash)
    finally:
        await transport.close()

    index.update({"head": commit_hash, "tree": new_tree})
    save_index(world_dir, index)
    add_known_objects(world_dir, new_known)

    parts = [f"{len(changed)} changed"]
    if deleted:
        parts.append(f"{len(deleted)} deleted")
    click.echo(f"[{commit_hash[:12]}] {', '.join(parts)}")
    return True


async def _pull_async(world_dir: Path, cfg: dict) -> bool:
    transport = await _connect(cfg)
    try:
        remote_head = await transport.get_head()
        if not remote_head:
            click.echo("Remote has no commits.")
            return False

        index = load_index(world_dir)
        if index.get("head") == remote_head:
            click.echo("Already up to date.")
            return False

        commit = json.loads(await transport.get_commit(remote_head))
        remote_tree = commit["tree"]

        to_fetch = [
            (rel, meta)
            for rel, meta in remote_tree.items()
            if index["tree"].get(rel, {}).get("sha256") != meta["sha256"]
        ]

        click.echo(f"Fetching {len(to_fetch)} manifest(s) via tar...")
        manifest_hashes = [meta["manifest"] for _, meta in to_fetch]
        raw_manifests = await transport.get_manifests(manifest_hashes)

        fetches = [
            (rel, meta, json.loads(raw_manifests[meta["manifest"]]))
            for rel, meta in to_fetch
        ]
        all_block_hashes = [bh for _, _, m in fetches for bh in m["blocks"]]
        total_blocks = len(all_block_hashes)

        click.echo(f"Fetching {total_blocks} block(s) via tar...")
        with click.progressbar(length=1, label="Downloading", width=40) as bar:
            fetched_blocks = await transport.get_blocks(all_block_hashes)
            bar.update(1)

        block_iter = iter(fetched_blocks)
        for rel, meta, manifest in fetches:
            blocks = [next(block_iter) for _ in manifest["blocks"]]
            file_data = b"".join(blocks)[: manifest["size"]]
            dest = world_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(file_data)

        for rel in list(index["tree"]):
            if rel not in remote_tree:
                p = world_dir / rel
                if p.exists():
                    p.unlink()

        index.update({"head": remote_head, "tree": remote_tree})
        save_index(world_dir, index)

        click.echo(f"[{remote_head[:12]}] Updated {len(to_fetch)} file(s).")
        return True
    finally:
        await transport.close()


def _push(world_dir: Path, cfg: dict, message: str = "auto") -> bool:
    return asyncio.run(_push_async(world_dir, cfg, message))


def _pull(world_dir: Path, cfg: dict) -> bool:
    return asyncio.run(_pull_async(world_dir, cfg))


# ── commands ──────────────────────────────────────────────────────────────────

@click.group()
def main():
    """GitCraft — Git-like sync for Minecraft worlds."""


@main.command()
@click.argument("name")
@click.argument("path", type=click.Path())
def init(name, path):
    """Register a world and initialize GitCraft in it.

    \b
    Example:
      gitcraft init survival ./saves/survival
    """
    world_dir = Path(path).resolve()
    world_dir.mkdir(parents=True, exist_ok=True)

    cfg_path = world_dir / ".gitcraft" / "config.toml"
    if not cfg_path.exists():
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(
            '[remote]\n'
            'host = "your-server.example.com"\n'
            'port = 22\n'
            'user = "minecraft"\n'
            'key_file = "~/.ssh/id_rsa"\n'
            f'path = "/srv/gitcraft/{name}"\n',
            encoding="utf-8",
        )

    reg.register(name, world_dir)
    click.echo(f"World '{name}' → {world_dir}")
    click.echo(f"Edit {cfg_path} to configure your remote.")


@main.command()
@click.argument("remote")
@click.argument("name")
@click.argument("path", default="", required=False)
@click.option("--port", "-p", default=22, show_default=True)
@click.option("--key", "-k", default="~/.ssh/id_rsa", show_default=True)
def clone(remote, name, path, port, key):
    """Clone a world from a remote into a local folder.

    \b
    REMOTE format:  user@host:/remote/path
    Example:
      gitcraft clone gitcraft@myserver.com:/srv/gitcraft/survival survival
      gitcraft clone gitcraft@myserver.com:/srv/gitcraft/survival survival ./saves/survival
    """
    if "@" not in remote or ":" not in remote:
        raise click.ClickException(
            "Remote must be in the format user@host:/remote/path"
        )
    user_host, remote_path = remote.split(":", 1)
    user, host = user_host.split("@", 1)

    world_dir = Path(path).resolve() if path else Path(name).resolve()
    world_dir.mkdir(parents=True, exist_ok=True)

    cfg_path = world_dir / ".gitcraft" / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        f'[remote]\n'
        f'host = "{host}"\n'
        f'port = {port}\n'
        f'user = "{user}"\n'
        f'key_file = "{key}"\n'
        f'path = "{remote_path}"\n',
        encoding="utf-8",
    )

    reg.register(name, world_dir)
    click.echo(f"Cloning '{name}' from {remote} into {world_dir} ...")
    _pull(world_dir, load_config(world_dir))


@main.command()
@click.argument("name")
@click.option("--message", "-m", default="auto")
def push(name, message):
    """Push local changes to the remote.

    \b
    Example:
      gitcraft push survival
    """
    world_dir = _world_dir(name)
    _push(world_dir, load_config(world_dir), message)


@main.command()
@click.argument("name")
def pull(name):
    """Pull the latest snapshot from the remote.

    \b
    Example:
      gitcraft pull survival
    """
    world_dir = _world_dir(name)
    _pull(world_dir, load_config(world_dir))


@main.command()
@click.argument("name")
def status(name):
    """Show local changes that would be pushed."""
    world_dir = _world_dir(name)
    index = load_index(world_dir)
    files = scan(world_dir)
    hashes = {rel: hash_file(p) for rel, p in files.items()}

    changed = [r for r in files if index["tree"].get(r, {}).get("sha256") != hashes[r]]
    deleted = [r for r in index["tree"] if r not in files]

    if not changed and not deleted:
        click.echo("Nothing to push.")
    else:
        for r in sorted(changed):
            click.echo(f"  M  {r}")
        for r in sorted(deleted):
            click.echo(f"  D  {r}")

    head = index.get("head") or "none"
    click.echo(f"\nHEAD: {head[:12] if head != 'none' else 'none'}")




@main.command()
@click.argument("name")
@click.option("--limit", "-n", default=10, show_default=True)
def log(name, limit):
    """Show commit history from the remote."""
    world_dir = _world_dir(name)
    cfg = load_config(world_dir)
    transport = _connect(cfg)
    try:
        current = transport.get_head()
        if not current:
            click.echo("No commits.")
            return
        for _ in range(limit):
            if not current:
                break
            commit = json.loads(transport.get_commit(current))
            ts = commit.get("timestamp", "?")
            msg = commit.get("message", "")
            n = len(commit.get("tree", {}))
            click.echo(f"{current[:12]}  {ts}  {n} files  {msg}")
            current = commit.get("parent")
    finally:
        transport.close()


@main.command()
def worlds():
    """List all registered worlds."""
    registry = reg.load()
    if not registry:
        click.echo("No worlds registered. Use 'gitcraft init <name> <path>'.")
        return
    for name, path in registry.items():
        marker = "✓" if Path(path).exists() else "✗"
        click.echo(f"  {marker}  {name:20s}  {path}")
