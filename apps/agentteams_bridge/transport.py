"""Small injectable HTTP transport used by live clients and contract tests."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Protocol


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class HTTPTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        json_body: Any = None,
        timeout: float = 15.0,
    ) -> HTTPResponse: ...


class TransportFailure(RuntimeError):
    """A request failed before an HTTP response was available."""


class UrllibTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        json_body: Any = None,
        timeout: float = 15.0,
    ) -> HTTPResponse:
        request_headers: Dict[str, str] = dict(headers or {})
        data: Optional[bytes] = None
        if json_body is not None:
            data = json.dumps(
                json_body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request_headers.setdefault("Accept", "application/json")
        request = urllib.request.Request(
            url=url,
            data=data,
            headers=request_headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HTTPResponse(
                    status=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except urllib.error.HTTPError as error:
            return HTTPResponse(
                status=error.code,
                headers=dict(error.headers.items()) if error.headers else {},
                body=error.read(),
            )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TransportFailure(str(error)) from error
