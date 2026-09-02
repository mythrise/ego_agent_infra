from __future__ import annotations

import io
import json
from typing import Any
from urllib.error import HTTPError

import pytest

from integrations.agentteams.model_gateway import (
    ModelGatewayError,
    OpenAICompatibleModelGateway,
    parse_json_object,
)


class Response(io.BytesIO):
    def __init__(self, payload: Any, status: int = 200) -> None:
        super().__init__(json.dumps(payload).encode())
        self.status = status

    def getcode(self) -> int:
        return self.status


def test_fenced_json_is_parsed() -> None:
    assert parse_json_object("```json\n{\"ok\": true}\n```") == {"ok": True}
    assert parse_json_object("```json\n{\"ok\": true}") == {"ok": True}


def test_non_object_model_output_is_rejected() -> None:
    with pytest.raises(ModelGatewayError, match="must be an object"):
        parse_json_object("[1, 2]")


def test_gateway_receipt_is_redacted_and_digest_bound() -> None:
    secret = "never-persist-this-key"
    request_payload: dict[str, Any] = {}

    def opener(request, timeout):
        nonlocal request_payload
        assert request.headers["Authorization"] == "Bearer " + secret
        if request.full_url.endswith("/models"):
            return Response({"data": [{"id": "model-a"}]})
        request_payload = json.loads(request.data)
        return Response(
            {
                "id": "response-1",
                "model": "model-a",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "```json\n{\"role\":\"scout\"}\n```"},
                    }
                ],
                "usage": {"total_tokens": 12},
            }
        )

    gateway = OpenAICompatibleModelGateway(
        "https://gateway.example/v1",
        secret,
        "model-a",
        reasoning_effort="low",
        opener=opener,
    )
    assert gateway.list_models() == ["model-a"]
    call = gateway.complete_json(
        role="scout", system_prompt="Return JSON", input_payload={"goal": "bounded"}
    )
    assert call.output == {"role": "scout"}
    assert request_payload["response_format"] == {"type": "json_object"}
    assert request_payload["reasoning_effort"] == "low"
    assert call.receipt["http_status"] == 200
    assert call.receipt["finish_reason"] == "stop"
    assert call.receipt["response_chars"] > 0
    assert call.receipt["json_mode"] is True
    assert call.receipt["reasoning_effort"] == "low"
    assert call.receipt["truth_boundary"].startswith("LIVE_MODEL_RESPONSE_ONLY")
    assert secret not in json.dumps(call.receipt)
    assert len(call.receipt["request_sha256"]) == 64
    assert len(call.receipt["response_sha256"]) == 64


def test_plain_http_remote_gateway_is_rejected() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        OpenAICompatibleModelGateway("http://gateway.example/v1", "key", "model")
    with pytest.raises(ValueError, match="HTTPS"):
        OpenAICompatibleModelGateway("http://localhost.evil.example/v1", "key", "model")


def test_url_embedded_credentials_are_rejected() -> None:
    with pytest.raises(ValueError, match="must not contain credentials"):
        OpenAICompatibleModelGateway("https://user:pass@gateway.example/v1", "key", "model")


def test_reasoning_budget_exhaustion_has_a_specific_error() -> None:
    def opener(request, timeout):
        return Response(
            {
                "id": "response-empty",
                "model": "model-a",
                "choices": [
                    {"finish_reason": "length", "message": {"content": ""}}
                ],
                "usage": {
                    "completion_tokens": 4096,
                    "completion_tokens_details": {"reasoning_tokens": 4096, "text_tokens": 0},
                },
            }
        )

    gateway = OpenAICompatibleModelGateway(
        "https://gateway.example/v1", "key", "model-a", opener=opener
    )
    with pytest.raises(ModelGatewayError, match="exhausted max_tokens in reasoning"):
        gateway.complete_json(
            role="experiment-architect",
            system_prompt="Return JSON",
            input_payload={"goal": "bounded"},
            max_tokens=4096,
        )


def test_invalid_reasoning_effort_is_rejected() -> None:
    with pytest.raises(ValueError, match="reasoning effort"):
        OpenAICompatibleModelGateway(
            "https://gateway.example/v1", "key", "model-a", reasoning_effort="extreme"
        )


def test_safe_provider_error_code_is_exposed_without_retrying_or_leaking_message() -> None:
    secret_message = "user balance and internal request details"

    def opener(request, timeout):
        payload = json.dumps(
            {
                "error": {
                    "code": "insufficient_user_quota",
                    "message": secret_message,
                }
            }
        ).encode()
        raise HTTPError(request.full_url, 403, "Forbidden", None, io.BytesIO(payload))

    gateway = OpenAICompatibleModelGateway(
        "https://gateway.example/v1", "key", "model-a", opener=opener
    )
    with pytest.raises(ModelGatewayError) as failure:
        gateway.complete_json(
            role="experiment-architect",
            system_prompt="Return JSON",
            input_payload={"goal": "bounded"},
        )

    assert str(failure.value) == "model gateway returned HTTP 403 (insufficient_user_quota)"
    assert failure.value.retryable is False
    assert secret_message not in str(failure.value)
