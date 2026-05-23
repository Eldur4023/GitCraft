# GitCraft

Sync Minecraft worlds across machines without uploading everything every time.
Inspired by Git and Steam Cloud Save — only the changed parts travel over the wire.

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
    └── blocks/3a/f7c2...       ← 4 KB chunks, gzip-compressed
```

**The server is a plain SSH/SFTP server. No GitCraft code runs there.**

## Requirements

- Python 3.11+
- An SSH server with SFTP access (the machine that stores the world — can be a cheap VPS)

## Install

```bash
pip install -e .
```

## Setup

### On the storage server (once)

```bash
useradd -m gitcraft
mkdir -p /srv/gitcraft/survival
chown gitcraft /srv/gitcraft/survival
# add your SSH public key to /home/gitcraft/.ssh/authorized_keys
```

### On each client machine

```bash
gitcraft init survival ./saves/survival
```

Then edit `./saves/survival/.gitcraft/config.toml`:

```toml
[remote]
host = "your-server.com"
port = 22
user = "gitcraft"
key_file = "~/.ssh/id_rsa"
path = "/srv/gitcraft/survival"
```

## Usage

```bash
# Stop the Minecraft server, then:
gitcraft push survival

# On another machine:
gitcraft pull survival
# Start the server.
```

```bash
gitcraft worlds             # list registered worlds
gitcraft status survival    # show what would be pushed
gitcraft log survival       # commit history
```

> **Important:** always stop the server before pushing or pulling.
> Minecraft writes `.mca` region files non-atomically — syncing while the server
> is running risks silent chunk corruption. GitCraft enforces this by checking
> for `session.lock` and aborting if it exists.

## Why not rsync / syncthing / git-lfs?

| Tool | Problem |
|---|---|
| rsync | uploads full files, no history |
| Syncthing | syncs while server runs → corruption |
| git-lfs | diffs text, not binary blocks; slow on large `.mca` |
| GitCraft | block-level dedup, history, safe by design |
