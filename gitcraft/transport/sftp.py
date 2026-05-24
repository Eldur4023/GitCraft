import asyncio
import gzip
from pathlib import PurePosixPath
from typing import Callable

import asyncssh


class AsyncSFTPTransport:
    def __init__(self, conn: asyncssh.SSHClientConnection, sftp: asyncssh.SFTPClient, base: str):
        self._conn = conn
        self._sftp = sftp
        self._base = PurePosixPath(base)
        self._created_prefixes: set[str] = set()
        self._prefix_lock = asyncio.Lock()

    @classmethod
    async def connect(
        cls,
        host: str,
        port: int,
        user: str,
        remote_path: str,
        key_file: str | None = None,
        password: str | None = None,
    ) -> "AsyncSFTPTransport":
        kwargs: dict = dict(host=host, port=port, username=user, known_hosts=None)
        if key_file:
            kwargs["client_keys"] = [str(key_file)]
        if password:
            kwargs["password"] = password

        conn = await asyncssh.connect(**kwargs)
        sftp = await conn.start_sftp_client()
        transport = cls(conn, sftp, remote_path)
        await transport._init_remote()
        return transport

    async def _init_remote(self) -> None:
        for sub in ["objects/commits", "objects/blocks", "objects/manifests"]:
            await self._sftp.makedirs(str(self._base / sub), exist_ok=True)

    def _p(self, *parts: str) -> str:
        return str(self._base.joinpath(*parts))

    async def _ensure_block_prefix(self, prefix: str) -> None:
        if prefix in self._created_prefixes:
            return
        async with self._prefix_lock:
            if prefix in self._created_prefixes:
                return
            try:
                await self._sftp.mkdir(self._p("objects/blocks", prefix))
            except asyncssh.SFTPError:
                pass  # already exists
            self._created_prefixes.add(prefix)

    # HEAD

    async def get_head(self) -> str | None:
        try:
            async with self._sftp.open(self._p("HEAD"), "r") as f:
                value = (await f.read()).strip()
                return value or None
        except asyncssh.SFTPNoSuchFile:
            return None

    async def set_head(self, commit_hash: str) -> None:
        async with self._sftp.open(self._p("HEAD"), "w") as f:
            await f.write(commit_hash)

    # Blocks

    async def put_block(self, block_hash: str, data: bytes) -> None:
        prefix = block_hash[:2]
        await self._ensure_block_prefix(prefix)
        compressed = gzip.compress(data, compresslevel=6)
        async with self._sftp.open(self._p("objects/blocks", prefix, block_hash[2:]), "wb") as f:
            await f.write(compressed)

    async def get_block(self, block_hash: str) -> bytes:
        async with self._sftp.open(
            self._p("objects/blocks", block_hash[:2], block_hash[2:]), "rb"
        ) as f:
            return gzip.decompress(await f.read())

    async def put_blocks(
        self,
        blocks: list[tuple[str, bytes]],
        on_progress: Callable | None = None,
        concurrency: int = 32,
    ) -> None:
        sem = asyncio.Semaphore(concurrency)

        async def upload(bh: str, bdata: bytes) -> None:
            async with sem:
                await self.put_block(bh, bdata)
                if on_progress:
                    on_progress()

        await asyncio.gather(*[upload(bh, bdata) for bh, bdata in blocks])

    async def get_blocks(
        self,
        hashes: list[str],
        on_progress: Callable | None = None,
        concurrency: int = 32,
    ) -> list[bytes]:
        sem = asyncio.Semaphore(concurrency)

        async def fetch(h: str) -> bytes:
            async with sem:
                data = await self.get_block(h)
                if on_progress:
                    on_progress()
                return data

        return await asyncio.gather(*[fetch(h) for h in hashes])

    # Manifests

    async def put_manifest(self, manifest_hash: str, data: bytes) -> None:
        async with self._sftp.open(self._p("objects/manifests", manifest_hash), "wb") as f:
            await f.write(data)

    async def get_manifest(self, manifest_hash: str) -> bytes:
        async with self._sftp.open(self._p("objects/manifests", manifest_hash), "rb") as f:
            return await f.read()

    # Commits

    async def put_commit(self, commit_hash: str, data: bytes) -> None:
        async with self._sftp.open(self._p("objects/commits", commit_hash), "wb") as f:
            await f.write(data)

    async def get_commit(self, commit_hash: str) -> bytes:
        async with self._sftp.open(self._p("objects/commits", commit_hash), "rb") as f:
            return await f.read()

    async def close(self) -> None:
        self._sftp.exit()
        self._conn.close()
