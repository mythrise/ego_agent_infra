"""The broker forwards only qualified requests without mutation."""

import os
import tempfile

import pytest

from benchmarks.secure_memory.models import ModelRequest
from benchmarks.secure_memory.substrate.broker import (
    BrokerDenied,
    BrokerState,
    ProviderCapabilityRecord,
    ProviderReply,
    ProviderRequestShape,
    ProviderBroker,
    read_authorized_secret_fd,
)
from tests.secure_memory.test_budget_ledger import SHA, _ledger


class FakeTransport:
    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply or ProviderReply(raw_usage={"input_tokens": 12, "output_tokens": 3}, output_text="ok")
    def send(self, *, method, endpoint, body, api_key, allow_redirects, tls_verified):
        self.calls.append((method, endpoint, body, api_key, allow_redirects, tls_verified))
        return self.reply


def _request(**overrides):
    values = dict(schema_version="secure-memory-model-request/v1", request_id="r1", campaign_id="campaign",
                  lease_sha256=SHA, ticket_id="ticket-1", request_class="main",
                  provider_base_url="https://apihub.agnes-ai.com/v1", provider_model="agnes-2.5-pro",
                  runtime="agentteams", messages=({"role": "user", "content": "hello"},),
                  max_input_tokens=10000, max_output_tokens=1500, temperature=0, top_p=1,
                  stream=True, tools=())
    values.update(overrides)
    return ModelRequest(**values)


def _capability(**overrides):
    values = dict(state=BrokerState.QUALIFIED, endpoint="/chat/completions", method="POST",
                  body_shape=ProviderRequestShape.CHAT_COMPLETIONS, model="agnes-2.5-pro",
                  hard_output_limit=True, role_attribution=True, authoritative_usage=True,
                  streaming_semantics=True, zero_background_calls=True)
    values.update(overrides)
    return ProviderCapabilityRecord(**values)


def test_locked_capability_rejects_before_transport():
    ledger, lease = _ledger()
    transport = FakeTransport()
    broker = ProviderBroker(ledger=ledger, capability=_capability(state=BrokerState.LOCKED), transport=transport,
                            signature_verifier=lambda _value: True, secret_fd=None)
    with pytest.raises(BrokerDenied, match="capability_locked"):
        broker.dispatch(_request(), lease=lease, requester_role="Worker")
    assert not transport.calls


def test_exact_qualified_request_is_forwarded_unchanged_with_terminal_usage():
    ledger, lease = _ledger()
    transport = FakeTransport()
    broker = ProviderBroker(ledger=ledger, capability=_capability(), transport=transport,
                            signature_verifier=lambda _value: True, secret_fd=None)
    response = broker.dispatch(_request(), lease=lease, requester_role="Worker")
    method, endpoint, body, _secret, redirects, tls = transport.calls[0]
    assert (method, endpoint, redirects, tls) == ("POST", "/chat/completions", False, True)
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["tools"] == []
    assert body["model"] == "agnes-2.5-pro"
    assert body["max_tokens"] == 1500
    assert response.usage.budget_input == 12
    assert response.first_stream_ns is not None
    assert response.first_content_ns is not None


def test_bad_signature_or_unqualified_shape_never_reaches_transport():
    ledger, lease = _ledger()
    transport = FakeTransport()
    broker = ProviderBroker(ledger=ledger, capability=_capability(), transport=transport,
                            signature_verifier=lambda _value: False, secret_fd=None)
    with pytest.raises(BrokerDenied, match="signature"):
        broker.dispatch(_request(), lease=lease, requester_role="Worker")
    assert not transport.calls
    broker = ProviderBroker(ledger=ledger, capability=_capability(endpoint="/other"), transport=transport,
                            signature_verifier=lambda _value: True, secret_fd=None)
    with pytest.raises(BrokerDenied, match="capability"):
        broker.dispatch(_request(), lease=lease, requester_role="Worker")


def test_transport_failure_retains_reservation_and_sanitizes_error():
    ledger, lease = _ledger()
    class BrokenTransport(FakeTransport):
        def send(self, **kwargs):
            raise RuntimeError("api-key=definitely-not-logged")
    broker = ProviderBroker(ledger=ledger, capability=_capability(), transport=BrokenTransport(),
                            signature_verifier=lambda _value: True, secret_fd=None)
    with pytest.raises(BrokerDenied, match="provider_failure") as error:
        broker.dispatch(_request(), lease=lease, requester_role="Worker")
    assert "definitely" not in str(error.value)
    assert ledger.reservation_for("ticket-1").state.value == "RETAINED"


def test_secret_reader_uses_an_already_open_fake_regular_descriptor_only():
    fd, path = tempfile.mkstemp()
    try:
        os.write(fd, b"fake-secret")
        os.lseek(fd, 0, os.SEEK_SET)
        assert read_authorized_secret_fd(fd, expected_uid=os.getuid()) == b"fake-secret"
    finally:
        os.close(fd)
        os.unlink(path)
