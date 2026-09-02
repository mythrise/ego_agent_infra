"""Small OpenAI-compatible model gateway for AgentTeams Worker backends.

This module deliberately does not claim that a successful model response is an
official AgentTeams Controller, Matrix, Worker ACK, or task receipt.  It is the
model-plane adapter used by the bounded local acceptance harness and can also be
pointed at the same provider configured behind an AgentTeams deployment.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional


class ModelGatewayError(RuntimeError):
    """Sanitized gateway failure that never includes credentials or response bodies."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parse_json_object(content: str) -> Dict[str, Any]:
    """Parse a JSON object, accepting the common fenced-JSON provider response."""

    stripped = content.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline < 0:
            raise ModelGatewayError("model response contains a malformed code fence")
        stripped = stripped[first_newline + 1 :].strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
    try:
        parsed, end = json.JSONDecoder().raw_decode(stripped)
    except (TypeError, json.JSONDecodeError) as error:
        raise ModelGatewayError("model response is not a valid JSON object") from error
    trailing = stripped[end:].strip()
    if trailing not in {"", "```"}:
        raise ModelGatewayError("model response contains text outside the JSON object")
    if not isinstance(parsed, dict):
        raise ModelGatewayError("model response JSON must be an object")
    return parsed


@dataclass(frozen=True)
class ModelCall:
    output: Dict[str, Any]
    receipt: Dict[str, Any]


class OpenAICompatibleModelGateway:
    """Credential-safe client for ``/models`` and ``/chat/completions``."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 60.0,
        reasoning_effort: Optional[str] = None,
        opener: Optional[Any] = None,
    ) -> None:
        normalized = base_url.strip().rstrip("/")
        parsed = urllib.parse.urlsplit(normalized)
        loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ValueError("model gateway must use HTTPS or an explicit loopback URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("model gateway URL must not contain credentials, query, or fragment")
        if not api_key:
            raise ValueError("model gateway API key is required")
        if not model.strip():
            raise ValueError("model name is required")
        normalized_effort = reasoning_effort.strip().lower() if reasoning_effort else None
        if normalized_effort not in {None, "low", "medium", "high"}:
            raise ValueError("reasoning effort must be low, medium, high, or omitted")
        self.base_url = normalized
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.reasoning_effort = normalized_effort
        self._api_key = api_key
        self._opener = opener or urllib.request.urlopen

    def _request(self, method: str, path: str, body: Optional[bytes] = None) -> tuple[int, bytes]:
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer %s" % self._api_key,
            "User-Agent": "EgoAgentOS-AgentTeams-ModelGateway/1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path, data=body, headers=headers, method=method
        )
        try:
            response = self._opener(request, timeout=self.timeout_seconds)
            with response:
                status = int(getattr(response, "status", response.getcode()))
                payload = response.read()
        except urllib.error.HTTPError as error:
            provider_code = None
            try:
                failure = json.loads(error.read(4096))
                candidate = failure.get("error", {}).get("code")
                if (
                    isinstance(candidate, str)
                    and 1 <= len(candidate) <= 64
                    and all(char.isalnum() or char in "._-" for char in candidate)
                ):
                    provider_code = candidate
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                provider_code = None
            message = "model gateway returned HTTP %d" % error.code
            if provider_code:
                message += " (%s)" % provider_code
            retryable = error.code in {408, 409, 425, 429} or error.code >= 500
            raise ModelGatewayError(message, retryable=retryable) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ModelGatewayError("model gateway request failed") from error
        if not 200 <= status < 300:
            raise ModelGatewayError("model gateway returned HTTP %d" % status)
        return status, payload

    def list_models(self) -> List[str]:
        _, raw = self._request("GET", "/models")
        try:
            payload = json.loads(raw)
            models = [item["id"] for item in payload["data"] if isinstance(item.get("id"), str)]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ModelGatewayError("model catalog response has an invalid shape") from error
        return models

    def complete_json(
        self,
        *,
        role: str,
        system_prompt: str,
        input_payload: Mapping[str, Any],
        max_tokens: int = 1200,
    ) -> ModelCall:
        request_payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(input_payload, ensure_ascii=False, sort_keys=True),
                },
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if self.reasoning_effort is not None:
            request_payload["reasoning_effort"] = self.reasoning_effort
        request_bytes = canonical_bytes(request_payload)
        started = time.monotonic()
        status, response_bytes = self._request("POST", "/chat/completions", request_bytes)
        latency_ms = round((time.monotonic() - started) * 1000)
        try:
            response = json.loads(response_bytes)
            choice = response["choices"][0]
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("content")
            if not content.strip() and choice.get("finish_reason") == "length":
                raise ModelGatewayError(
                    "model exhausted max_tokens in reasoning before producing JSON text"
                )
            output = parse_json_object(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise ModelGatewayError("chat completion response has an invalid shape") from error
        receipt = {
            "schema": "egoagentos.model-gateway-receipt/v1",
            "source": "openai-compatible-model-gateway",
            "truth_boundary": (
                "LIVE_MODEL_RESPONSE_ONLY; not an official AgentTeams, Matrix, or GPU receipt"
            ),
            "role": role,
            "model": response.get("model") or self.model,
            "response_id": response.get("id"),
            "endpoint": self.base_url + "/chat/completions",
            "http_status": status,
            "request_sha256": sha256_bytes(request_bytes),
            "response_sha256": sha256_bytes(response_bytes),
            "latency_ms": latency_ms,
            "finish_reason": choice.get("finish_reason"),
            "response_chars": len(content),
            "json_mode": True,
            "reasoning_effort": self.reasoning_effort,
            "usage": response.get("usage") or {},
        }
        return ModelCall(output=output, receipt=receipt)
