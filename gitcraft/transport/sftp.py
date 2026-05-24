import gzip
import io
import tarfile
from typing import Callable

import asyncssh


class SSHTransport:
    def __init__(self, conn: asyncssh.SSHClientConnection, base: str):
        self._conn = conn
        self._base = base.rstrip("/")

    @classmethod
    async def connect(
        cls,
        host: str,
        port: int,
        user: str,
        remote_path: str,
        key_file: str | None = None,
        password: str | None = None,
    ) -> "SSHTransport":
        kwargs: dict = dict(host=host, port=port, username=user, known_hosts=None)
        if key_file:
            kwargs["client_keys"] = [str(key_file)]
        if password:
            kwargs["password"] = password
        conn = await asyncssh.connect(**kwargs)
        t = cls(conn, remote_path)
        await t._init_remote()
        return t

    async def _run(self, cmd: str, input: bytes | str | None = None) -> bytes:
        if isinstance(input, str):
            input = input.encode()
        result = await self._conn.run(cmd, input=input, encoding=None, check=False)
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode(errors="replace").strip()
            raise RuntimeError(f"Remote command failed [{result.returncode}]: {stderr}")
        return result.stdout or b""

    async def _init_remote(self) -> None:
        await self._run(
            f"mkdir -p {self._base}/objects/commits "
            f"{self._base}/objects/blocks "
            f"{self._base}/objects/manifests"
        )

    # ── HEAD ──────────────────────────────────────────────────────────────────

    async def get_head(self) -> str | None:
        out = (await self._run(f"cat {self._base}/HEAD 2>/dev/null || true")).decode().strip()
        return out or None

    async def set_head(self, commit_hash: str) -> None:
        await self._run(f"cat > {self._base}/HEAD", input=commit_hash)

    # ── Commits ───────────────────────────────────────────────────────────────

    async def get_commit(self, commit_hash: str) -> bytes:
        return await self._run(f"cat {self._base}/objects/commits/{commit_hash}")

    async def put_commit(self, commit_hash: str, data: bytes) -> None:
        await self._run(f"cat > {self._base}/objects/commits/{commit_hash}", input=data)

    # ── tar helpers ───────────────────────────────────────────────────────────

    async def _tar_get(self, rel_paths: list[str]) -> dict[str, bytes]:
        """Fetch remote files in one tar pipe. Returns {rel_path: raw_bytes}."""
        file_list = "\n".join(rel_paths)
        stdout = await self._run(
            f"tar cf - -C {self._base} -T -",
            input=file_list,
        )
        result: dict[str, bytes] = {}
        with tarfile.open(fileobj=io.BytesIO(stdout), mode="r") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    f = tar.extractfile(member)
                    if f:
                        result[member.name] = f.read()
        return result

    async def _tar_put(self, files: dict[str, bytes]) -> None:
        """Upload files to the remote base in one tar pipe."""
        buf = io.BytesIO()
        seen_dirs: set[str] = set()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for rel, data in files.items():
                parent = "/".join(rel.split("/")[:-1])
                if parent and parent not in seen_dirs:
                    di = tarfile.TarInfo(name=parent)
                    di.type = tarfile.DIRTYPE
                    di.mode = 0o755
                    tar.addfile(di)
                    seen_dirs.add(parent)
                fi = tarfile.TarInfo(name=rel)
                fi.size = len(data)
                tar.addfile(fi, io.BytesIO(data))
        await self._run(f"tar xf - -C {self._base}", input=buf.getvalue())

    # ── Blocks ────────────────────────────────────────────────────────────────

    def _block_path(self, h: str) -> str:
        return f"objects/blocks/{h[:2]}/{h[2:]}"

    async def get_blocks(
        self,
        hashes: list[str],
        on_progress: Callable | None = None,
    ) -> list[bytes]:
        if not hashes:
            return []
        raw = await self._tar_get([self._block_path(h) for h in hashes])
        result = []
        for h in hashes:
            data = gzip.decompress(raw[self._block_path(h)])
            result.append(data)
            if on_progress:
                on_progress()
        return result

    async def put_blocks(
        self,
        blocks: list[tuple[str, bytes]],
        on_progress: Callable | None = None,
    ) -> None:
        if not blocks:
            return
        files = {
            self._block_path(bh): gzip.compress(data, compresslevel=6)
            for bh, data in blocks
        }
        await self._tar_put(files)
        if on_progress:
            for _ in blocks:
                on_progress()

    # ── Manifests ─────────────────────────────────────────────────────────────

    def _manifest_path(self, h: str) -> str:
        return f"objects/manifests/{h}"

    async def get_manifests(self, hashes: list[str]) -> dict[str, bytes]:
        if not hashes:
            return {}
        raw = await self._tar_get([self._manifest_path(h) for h in hashes])
        return {h: raw[self._manifest_path(h)] for h in hashes}

    async def put_manifests(self, manifests: list[tuple[str, bytes]]) -> None:
        if not manifests:
            return
        await self._tar_put({self._manifest_path(mh): data for mh, data in manifests})

    async def close(self) -> None:
        self._conn.close()
