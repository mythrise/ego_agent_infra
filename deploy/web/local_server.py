#!/usr/bin/env python3
"""Serve the built EgoAgentOS SPA and proxy its same-origin API locally."""

from __future__ import annotations

import http.client
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit


WEB_ROOT = Path(os.environ.get("EGO_WEB_ROOT", "/usr/share/egoagentos"))
BACKEND_HOST = os.environ.get("EGO_WEB_BACKEND_HOST", "backend")
BACKEND_PORT = int(os.environ.get("EGO_WEB_BACKEND_PORT", "8000"))
MAX_REQUEST_BYTES = 8 * 1024 * 1024
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class EgoWebHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, _format: str, *_args: object) -> None:
        # API requests can contain operator credentials; keep the server silent.
        return

    def _content_length(self) -> Optional[int]:
        try:
            return int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "invalid Content-Length")
            return None

    def _proxy_api(self) -> None:
        content_length = self._content_length()
        if content_length is None:
            return
        if content_length > MAX_REQUEST_BYTES:
            self.send_error(413, "request body too large")
            return
        request_body = self.rfile.read(content_length) if content_length else None
        request_headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in HOP_BY_HOP_HEADERS
            and name.lower() not in {"host", "content-length"}
        }
        request_headers["Host"] = f"{BACKEND_HOST}:{BACKEND_PORT}"
        if request_body is not None:
            request_headers["Content-Length"] = str(len(request_body))

        upstream = http.client.HTTPConnection(BACKEND_HOST, BACKEND_PORT, timeout=65)
        try:
            upstream.request(self.command, self.path, body=request_body, headers=request_headers)
            response = upstream.getresponse()
            response_body = response.read()
            self.send_response(response.status, response.reason)
            for name, value in response.getheaders():
                if name.lower() not in HOP_BY_HOP_HEADERS and name.lower() != "content-length":
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response_body)
        except (OSError, http.client.HTTPException):
            self.send_error(502, "API upstream unavailable")
        finally:
            upstream.close()
            self.close_connection = True

    def _serve_spa(self, *, head_only: bool = False) -> None:
        if self.path == "/healthz":
            payload = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if not head_only:
                self.wfile.write(payload)
            return

        relative = urlsplit(self.path).path.lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        try:
            target.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self.send_error(404)
            return
        if relative and target.is_file():
            if head_only:
                super().do_HEAD()
            else:
                super().do_GET()
            return
        self.path = "/index.html"
        if head_only:
            super().do_HEAD()
        else:
            super().do_GET()

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy_api()
        else:
            self._serve_spa()

    def do_HEAD(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy_api()
        else:
            self._serve_spa(head_only=True)

    def do_POST(self) -> None:
        self._proxy_api()

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST
    do_OPTIONS = do_POST


if __name__ == "__main__":
    if not (WEB_ROOT / "index.html").is_file():
        raise SystemExit("built web assets are missing")
    ThreadingHTTPServer(("0.0.0.0", 80), EgoWebHandler).serve_forever()
