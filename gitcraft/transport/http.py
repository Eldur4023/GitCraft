import gzip
import io
import logging
import tarfile
from typing import Callable

import httpx

logger = logging.getLogger(__name__)


class HTTPTransport:
    def __init__(self, base_url: str, token: str):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(15.0, read=300.0, write=300.0),
        )

    # ── HEAD ──────────────────────────────────────────────────────────────────

    async def get_head(self) -> str | None:
        r = await self._client.get("/head")
        r.raise_for_status()
        return r.text.strip() or None

    async def set_head(self, commit_hash: str) -> None:
        (await self._client.put("/head", content=commit_hash.encode())).raise_for_status()

    # ── tar helpers ───────────────────────────────────────────────────────────

    async def _fetch(self, paths: list[str]) -> dict[str, bytes]:
        r = await self._client.post("/fetch", json={"paths": paths})
        r.raise_for_status()
        result: dict[str, bytes] = {}
        with tarfile.open(fileobj=io.BytesIO(r.content), mode="r") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    f = tar.extractfile(member)
                    if f:
                        result[member.name] = f.read()
        return result

    async def _push_tar(self, files: dict[str, bytes]) -> None:
        buf = io.BytesIO()
        seen_dirs: set[str] = set()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for path, data in sorted(files.items()):
                parent = "/".join(path.split("/")[:-1])
                if parent and parent not in seen_dirs:
                    di = tarfile.TarInfo(name=parent)
                    di.type = tarfile.DIRTYPE
                    di.mode = 0o755
                    tar.addfile(di)
                    seen_dirs.add(parent)
                fi = tarfile.TarInfo(name=path)
                fi.size = len(data)
                tar.addfile(fi, io.BytesIO(data))
        (await self._client.post("/push", content=buf.getvalue())).raise_for_status()

    # ── Commits ───────────────────────────────────────────────────────────────

    async def get_commit(self, commit_hash: str) -> bytes:
        raw = await self._fetch([f"objects/commits/{commit_hash}"])
        return raw[f"objects/commits/{commit_hash}"]

    async def put_commit(self, commit_hash: str, data: bytes) -> None:
        await self._push_tar({f"objects/commits/{commit_hash}": data})

    # ── Blocks ────────────────────────────────────────────────────────────────

    def _block_path(self, h: str) -> str:
        return f"objects/blocks/{h[:2]}/{h[2:]}"

    async def get_blocks(
        self,
        hashes: list[str],
        on_progress: Callable[[int], None] | None = None,
        batch_size: int = 64,
    ) -> list[bytes | None]:
        if not hashes:
            return []
        result: dict[str, bytes | None] = {}
        for i in range(0, len(hashes), batch_size):
            batch = hashes[i : i + batch_size]
            raw = await self._fetch([self._block_path(h) for h in batch])
            for h in batch:
                path = self._block_path(h)
                if path in raw:
                    result[h] = gzip.decompress(raw[path])
                else:
                    logger.warning("Missing block on remote: %s", path)
                    result[h] = None
            if on_progress:
                on_progress(len(batch))
        return [result[h] for h in hashes]

    async def put_blocks(
        self,
        blocks: list[tuple[str, bytes]],
        on_progress: Callable[[int], None] | None = None,
        batch_size: int = 64,
    ) -> None:
        if not blocks:
            return
        for i in range(0, len(blocks), batch_size):
            batch = blocks[i : i + batch_size]
            files = {
                self._block_path(bh): gzip.compress(data, compresslevel=6)
                for bh, data in batch
            }
            await self._push_tar(files)
            if on_progress:
                on_progress(len(batch))

    # ── Manifests ─────────────────────────────────────────────────────────────

    def _manifest_path(self, h: str) -> str:
        return f"objects/manifests/{h}"

    async def get_manifests(self, hashes: list[str]) -> dict[str, bytes]:
        if not hashes:
            return {}
        raw = await self._fetch([self._manifest_path(h) for h in hashes])
        return {h: raw[self._manifest_path(h)] for h in hashes}

    async def put_manifests(self, manifests: list[tuple[str, bytes]]) -> None:
        if not manifests:
            return
        await self._push_tar({self._manifest_path(mh): data for mh, data in manifests})

    async def close(self) -> None:
        await self._client.aclose()
