import asyncio
import json
import secrets
from pathlib import Path

import click

from . import registry as reg
from .config import load_config
from .core.blocks import hash_file, split_blocks
from .core.index import add_known_objects, load_index, load_known_objects, save_index
from .core.objects import make_commit, make_manifest
from .core.snapshot import scan


# ── transport factory ─────────────────────────────────────────────────────────

async def _connect(cfg: dict):
    r = cfg["remote"]
    if "url" in r:
        from .transport.http import HTTPTransport
        return HTTPTransport(r["url"], r.get("token", ""))
    else:
        try:
            from .transport.sftp import SSHTransport
        except ImportError:
            raise click.ClickException(
                "SSH transport requires asyncssh: pip install asyncssh"
            )
        return await SSHTransport.connect(
            host=r["host"],
            port=r.get("port", 22),
            user=r["user"],
            remote_path=r["path"],
            key_file=r.get("key_file"),
            password=r.get("password"),
        )


# ── push / pull ───────────────────────────────────────────────────────────────

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

    transport = await _connect(cfg)
    try:
        if blocks_to_upload:
            click.echo(f"Uploading {len(blocks_to_upload)} block(s)...")
            with click.progressbar(length=len(blocks_to_upload), width=40) as bar:
                await transport.put_blocks(
                    blocks_to_upload,
                    on_progress=lambda n: bar.update(n),
                )
        if manifests_to_upload:
            click.echo(f"Uploading {len(manifests_to_upload)} manifest(s)...")
            await transport.put_manifests(manifests_to_upload)

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

        manifest_hashes = [meta["manifest"] for _, meta in to_fetch]
        with click.progressbar(
            length=len(manifest_hashes), label=f"Manifests", width=40
        ) as bar:
            raw_manifests = await transport.get_manifests(manifest_hashes)
            bar.update(len(manifest_hashes))

        fetches = [
            (rel, meta, json.loads(raw_manifests[meta["manifest"]]))
            for rel, meta in to_fetch
        ]
        all_block_hashes = [bh for _, _, m in fetches for bh in m["blocks"]]
        total_blocks = len(all_block_hashes)

        with click.progressbar(
            length=total_blocks, label=f"Blocks   ", width=40
        ) as bar:
            fetched_blocks = await transport.get_blocks(
                all_block_hashes,
                on_progress=lambda n: bar.update(n),
            )

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


# ── helpers ───────────────────────────────────────────────────────────────────

def _world_dir(name: str) -> Path:
    try:
        return reg.resolve(name)
    except KeyError:
        raise click.ClickException(
            f"Unknown world '{name}'. Run 'gitcraft init {name} <path>' first."
        )


# ── commands ──────────────────────────────────────────────────────────────────

@click.group()
def main():
    """GitCraft — Git-like sync for Minecraft worlds."""


@main.command()
@click.argument("name", required=False)
@click.argument("path", required=False, type=click.Path())
@click.argument("remote", required=False, metavar="[user@host:/remote/path]")
@click.option("--port", "-p", default=8765, show_default=True, help="Port for the HTTP server on the remote.")
@click.option("--key", "-k", default=None, help="SSH private key for remote setup.")
@click.option("--password", default=None, help="SSH password for remote setup.")
def init(name, path, remote, port, key, password):
    """Register a world and set up the remote server.

    \b
    With a remote (full setup — SSHes in, starts server, writes config):
      gitcraft init survival ~/.minecraft/saves/survival user@host:/srv/gitcraft/survival

    Without a remote (local only — edit config.toml manually afterwards):
      gitcraft init survival ~/.minecraft/saves/survival

    With no arguments, opens an interactive GUI wizard.
    """
    if not name or not path:
        try:
            from .gui import run_init_wizard
        except ImportError:
            raise click.ClickException(
                "GUI requires customtkinter: pip install customtkinter"
            )
        params = run_init_wizard()
        if params is None:
            return
        name = params.name
        path = params.path
        remote = params.remote or None
        port = params.port
        key = params.key or None
        password = params.password or None

    world_dir = Path(path).resolve()
    world_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = world_dir / ".gitcraft" / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    if remote:
        if "@" not in remote or ":" not in remote:
            raise click.ClickException("Remote must be user@host:/path")
        user_host, remote_path = remote.split(":", 1)
        user, host = user_host.split("@", 1)
        token = secrets.token_urlsafe(24)

        click.echo(f"Connecting to {host}...")
        try:
            import asyncssh
        except ImportError:
            raise click.ClickException(
                "Remote setup requires asyncssh: pip install asyncssh"
            )

        async def _setup():
            connect_kwargs: dict = dict(
                host=host, username=user, known_hosts=None
            )
            if password:
                connect_kwargs["password"] = password
            elif key:
                connect_kwargs["client_keys"] = [key]

            async with asyncssh.connect(**connect_kwargs) as conn:
                cmd = (
                    f"mkdir -p {remote_path} && "
                    f"nohup gitcraft serve {remote_path} --port {port} --token {token} "
                    f"> {remote_path}/serve.log 2>&1 & echo $!"
                )
                result = await conn.run(cmd, check=True)
                return result.stdout.strip()

        pid = asyncio.run(_setup())
        url = f"http://{host}:{port}"
        cfg_path.write_text(
            f'[remote]\nurl = "{url}"\ntoken = "{token}"\n',
            encoding="utf-8",
        )
        click.echo(f"Remote server started (pid {pid}) → {url}")
    else:
        if not cfg_path.exists():
            cfg_path.write_text(
                '[remote]\n'
                'url = "http://your-server.example.com:8765"\n'
                'token = "changeme"\n',
                encoding="utf-8",
            )
        click.echo(f"Edit {cfg_path} to configure your remote.")

    reg.register(name, world_dir)
    click.echo(f"World '{name}' → {world_dir}")


@main.command()
@click.argument("remote")
@click.argument("name")
@click.argument("path", default="", required=False)
@click.option("--token", "-t", default="", help="Bearer token (HTTP remotes)")
@click.option("--ssh-port", default=22, show_default=True)
@click.option("--key", "-k", default="~/.ssh/id_rsa", show_default=True)
def clone(remote, name, path, token, ssh_port, key):
    """Clone a world from a remote.

    \b
    HTTP remote:
      gitcraft clone http://myserver.com:8765 survival --token SECRET
    SSH remote (legacy):
      gitcraft clone user@host:/remote/path survival
    """
    world_dir = Path(path).resolve() if path else Path(name).resolve()
    world_dir.mkdir(parents=True, exist_ok=True)

    cfg_path = world_dir / ".gitcraft" / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    if remote.startswith("http://") or remote.startswith("https://"):
        cfg_path.write_text(
            f'[remote]\nurl = "{remote}"\ntoken = "{token}"\n',
            encoding="utf-8",
        )
    else:
        if "@" not in remote or ":" not in remote:
            raise click.ClickException(
                "SSH remote must be user@host:/path or an http(s):// URL."
            )
        user_host, remote_path = remote.split(":", 1)
        user, host = user_host.split("@", 1)
        cfg_path.write_text(
            f'[remote]\nhost = "{host}"\nport = {ssh_port}\n'
            f'user = "{user}"\nkey_file = "{key}"\npath = "{remote_path}"\n',
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
    async def _run():
        world_dir = _world_dir(name)
        transport = await _connect(load_config(world_dir))
        try:
            current = await transport.get_head()
            if not current:
                click.echo("No commits.")
                return
            for _ in range(limit):
                if not current:
                    break
                commit = json.loads(await transport.get_commit(current))
                ts = commit.get("timestamp", "?")
                msg = commit.get("message", "")
                n = len(commit.get("tree", {}))
                click.echo(f"{current[:12]}  {ts}  {n} files  {msg}")
                current = commit.get("parent")
        finally:
            await transport.close()

    asyncio.run(_run())


@main.command()
@click.argument("path", type=click.Path())
@click.option("--port", default=8765, show_default=True)
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--token", envvar="GITCRAFT_TOKEN", required=True,
              help="Bearer token for auth. Also read from GITCRAFT_TOKEN env var.")
def serve(path, port, host, token):
    """Run the GitCraft HTTP server at PATH.

    \b
    PATH is the remote objects directory — it can be anywhere.
    Example:
      gitcraft serve /srv/minecraft/worlds/survival --token s3cr3t
      GITCRAFT_TOKEN=s3cr3t gitcraft serve /srv/minecraft/worlds/survival
    """
    from .server import run_server
    run_server(Path(path), host, port, token)


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
