from __future__ import annotations

import io
import json
from typing import Any

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

    def opener(request, timeout):
        assert request.headers["Authorization"] == "Bearer " + secret
        if request.full_url.endswith("/models"):
            return Response({"data": [{"id": "model-a"}]})
        return Response(
            {
                "id": "response-1",
                "model": "model-a",
                "choices": [{"message": {"content": "```json\n{\"role\":\"scout\"}\n```"}}],
                "usage": {"total_tokens": 12},
            }
        )

    gateway = OpenAICompatibleModelGateway(
        "https://gateway.example/v1", secret, "model-a", opener=opener
    )
    assert gateway.list_models() == ["model-a"]
    call = gateway.complete_json(
        role="scout", system_prompt="Return JSON", input_payload={"goal": "bounded"}
    )
    assert call.output == {"role": "scout"}
    assert call.receipt["http_status"] == 200
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
