# GitCraft

Sync Minecraft worlds across machines without uploading everything every time.
Inspired by Git — only the changed parts travel over the wire.

## How it works

When you push, GitCraft splits each world file into 512 KB blocks and computes a SHA-256 for each one.
Only blocks the server has never seen before get uploaded.
A commit object records the current state of the world (like a Git tree), and `HEAD` on the server always points to the latest one.

On pull, the client downloads the remote commit, compares it to its local state, and fetches only the blocks it's missing.

```
/srv/gitcraft/survival/
├── HEAD                        ← hash of the latest commit
└── objects/
    ├── commits/abc123...       ← {parent, timestamp, tree}
    ├── manifests/def456...     ← block list for each file version
    └── blocks/3a/f7c2...       ← 512 KB chunks, gzip-compressed
```

All transfers happen over HTTP in batches — push and pull are a handful of requests regardless of how many files changed.

## Requirements

**Client:** Python 3.10+, `pip install gitcraft`

**Server:** Python 3.10+, `pip install gitcraft`

## Install

```bash
pip install gitcraft
```

## Setup

### The easy way — GUI wizard

Run `gitcraft init` with no arguments to open the setup wizard:

```bash
gitcraft init
```

Fill in the world name, local path, and optionally the remote (`user@host:/path`), port, and SSH credentials (key file or password). GitCraft will SSH into the server, start the HTTP server in the background, and write the config automatically.

### The manual way — CLI

```bash
gitcraft init survival ~/.minecraft/saves/survival user@host:/srv/gitcraft/survival
```

This does the same as the wizard: SSHes into the server, creates the directory, starts `gitcraft serve` in the background, and writes `~/.minecraft/saves/survival/.gitcraft/config.toml` with the URL and token.

With a password instead of an SSH key:

```bash
gitcraft init survival ~/.minecraft/saves/survival user@host:/srv/gitcraft/survival --password mypass
```

With a specific key:

```bash
gitcraft init survival ~/.minecraft/saves/survival user@host:/srv/gitcraft/survival --key ~/.ssh/minecraft_rsa
```

Without a remote (write config manually afterwards):

```bash
gitcraft init survival ~/.minecraft/saves/survival
# Edit ~/.minecraft/saves/survival/.gitcraft/config.toml
```

### Cloning an existing remote

If the server is already running and the world has data:

```bash
gitcraft clone http://your-server.com:8765 survival ~/.minecraft/saves/survival --token yourtoken
```

### Starting the server manually

If you prefer to start the server yourself (e.g. via systemd or tmux):

```bash
gitcraft serve /srv/gitcraft/survival --port 8765 --token yourtoken
# or with an env var:
GITCRAFT_TOKEN=yourtoken gitcraft serve /srv/gitcraft/survival
```

The path can be anywhere — it doesn't need to be next to your world files. For multiple worlds, run one instance per world on a different port.

## Usage

```bash
# Stop the Minecraft server, then:
gitcraft push survival

# On another machine:
gitcraft pull survival
# Start the server again.
```

```bash
gitcraft worlds             # list registered worlds
gitcraft status survival    # show what would be pushed
gitcraft log survival       # commit history
```

> **Important:** always stop the Minecraft server before pushing or pulling.
> Minecraft writes `.mca` region files non-atomically — syncing while the server
> is running risks silent chunk corruption. GitCraft does not enforce this, so be careful!

## Migrating from SSH transport

If you were using the old SSH/SFTP config, update your `.gitcraft/config.toml`:

```toml
# Old (SSH/SFTP)
[remote]
host = "your-server.com"
user = "gitcraft"
path = "/srv/gitcraft/survival"

# New (HTTP) — start gitcraft serve on the same path, then:
[remote]
url = "http://your-server.com:8765"
token = "yourtoken"
```

The object store on disk is identical — no data migration needed.

## Why not rsync / syncthing / git-lfs?

| Tool | Problem |
|---|---|
| rsync | uploads full files, no history |
| Syncthing | syncs while server runs → corruption |
| git-lfs | diffs text, not binary blocks; slow on large `.mca` |
| GitCraft | block-level dedup, history, HTTP transport |
