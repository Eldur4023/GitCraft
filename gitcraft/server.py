"""GitCraft HTTP server — run on the remote host with `gitcraft serve`."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import sys
import tarfile
from pathlib import Path


def _make_handler(base: Path, token: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _auth(self) -> bool:
            return self.headers.get("Authorization", "") == f"Bearer {token}"

        def _send(self, code: int, body: bytes, ctype: str = "application/octet-stream") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> bytes:
            return self.rfile.read(int(self.headers.get("Content-Length", 0)))

        def do_GET(self):
            if not self._auth():
                return self._send(401, b"Unauthorized")
            if self.path == "/head":
                p = base / "HEAD"
                self._send(200, p.read_bytes() if p.exists() else b"")
            else:
                self._send(404, b"Not found")

        def do_PUT(self):
            if not self._auth():
                return self._send(401, b"Unauthorized")
            if self.path == "/head":
                (base / "HEAD").write_bytes(self._body())
                self._send(200, b"OK")
            else:
                self._send(404, b"Not found")

        def do_POST(self):
            if not self._auth():
                return self._send(401, b"Unauthorized")

            if self.path == "/fetch":
                paths = json.loads(self._body()).get("paths", [])
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w") as tar:
                    for rel in paths:
                        # guard against path traversal
                        full = (base / rel).resolve()
                        if not str(full).startswith(str(base)):
                            continue
                        if full.is_file():
                            tar.add(full, arcname=rel)
                self._send(200, buf.getvalue())

            elif self.path == "/push":
                data = self._body()
                with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
                    for member in tar.getmembers():
                        # guard against path traversal
                        dest = (base / member.name).resolve()
                        if not str(dest).startswith(str(base)):
                            continue
                        if member.isdir():
                            dest.mkdir(parents=True, exist_ok=True)
                        elif member.isfile():
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            f = tar.extractfile(member)
                            if f:
                                dest.write_bytes(f.read())
                self._send(200, b"OK")

            else:
                self._send(404, b"Not found")

    return Handler


def run_server(base: Path, host: str, port: int, token: str) -> None:
    base = base.resolve()
    for sub in ["objects/commits", "objects/blocks", "objects/manifests"]:
        (base / sub).mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((host, port), _make_handler(base, token))
    click_echo = _try_click_echo()
    click_echo(f"Serving {base} on {host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def _try_click_echo():
    try:
        import click
        return click.echo
    except ImportError:
        return print
