#!/usr/bin/env python3
"""Local-only HTTP proxy for the official AgentTeams Controller.

The official local deployment exposes its gateway but keeps the Controller API
inside ``agentteams-net``. Docker Compose publishes this proxy exclusively on
127.0.0.1 so local tooling can call the Controller without changing the
official AgentTeams containers or images.
"""

from __future__ import annotations

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


UPSTREAM_HOST = "agentteams-controller"
UPSTREAM_PORT = 8090
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


class ControllerProxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        # Authorization headers and request bodies must never reach logs.
        return

    def _forward(self) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "invalid Content-Length")
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
        request_headers["Host"] = f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"
        if request_body is not None:
            request_headers["Content-Length"] = str(len(request_body))

        upstream = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=65)
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
            self.send_error(502, "Controller upstream unavailable")
        finally:
            upstream.close()
            self.close_connection = True

    do_GET = _forward
    do_HEAD = _forward
    do_POST = _forward
    do_PUT = _forward
    do_PATCH = _forward
    do_DELETE = _forward
    do_OPTIONS = _forward


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), ControllerProxy).serve_forever()
