import gzip
import io
from pathlib import PurePosixPath

import paramiko


class SFTPTransport:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        remote_path: str,
        key_file: str | None = None,
        password: str | None = None,
    ):
        self._base = PurePosixPath(remote_path)
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._client.connect(
            host,
            port=port,
            username=user,
            password=password or None,
            key_filename=key_file or None,
        )
        self._sftp = self._client.open_sftp()
        self._init_remote()

    def _init_remote(self) -> None:
        for sub in ["objects/commits", "objects/blocks", "objects/manifests"]:
            self._mkdirp(str(self._base / sub))

    def _mkdirp(self, path: str) -> None:
        parts = list(PurePosixPath(path).parts)
        for i in range(1, len(parts) + 1):
            sub = str(PurePosixPath(*parts[:i]))
            try:
                self._sftp.stat(sub)
            except FileNotFoundError:
                try:
                    self._sftp.mkdir(sub)
                except OSError:
                    pass  # concurrent mkdir race is fine

    def _p(self, *parts: str) -> str:
        return str(self._base.joinpath(*parts))

    # HEAD

    def get_head(self) -> str | None:
        try:
            with self._sftp.open(self._p("HEAD"), "r") as f:
                value = f.read().decode().strip()
                return value or None
        except FileNotFoundError:
            return None

    def set_head(self, commit_hash: str) -> None:
        with self._sftp.open(self._p("HEAD"), "w") as f:
            f.write(commit_hash.encode())

    # Blocks (gzip-compressed, 2-char prefix sharding)

    def put_block(self, block_hash: str, data: bytes) -> None:
        prefix = block_hash[:2]
        dir_path = self._p("objects/blocks", prefix)
        try:
            self._sftp.stat(dir_path)
        except FileNotFoundError:
            self._sftp.mkdir(dir_path)
        compressed = gzip.compress(data, compresslevel=6)
        self._sftp.putfo(io.BytesIO(compressed), self._p("objects/blocks", prefix, block_hash[2:]))

    def get_block(self, block_hash: str) -> bytes:
        buf = io.BytesIO()
        self._sftp.getfo(self._p("objects/blocks", block_hash[:2], block_hash[2:]), buf)
        return gzip.decompress(buf.getvalue())

    # Manifests (plain JSON, small)

    def put_manifest(self, manifest_hash: str, data: bytes) -> None:
        self._sftp.putfo(io.BytesIO(data), self._p("objects/manifests", manifest_hash))

    def get_manifest(self, manifest_hash: str) -> bytes:
        buf = io.BytesIO()
        self._sftp.getfo(self._p("objects/manifests", manifest_hash), buf)
        return buf.getvalue()

    # Commits (plain JSON)

    def put_commit(self, commit_hash: str, data: bytes) -> None:
        self._sftp.putfo(io.BytesIO(data), self._p("objects/commits", commit_hash))

    def get_commit(self, commit_hash: str) -> bytes:
        buf = io.BytesIO()
        self._sftp.getfo(self._p("objects/commits", commit_hash), buf)
        return buf.getvalue()

    def close(self) -> None:
        self._sftp.close()
        self._client.close()
