"""The broker forwards only qualified requests without mutation."""

import os
import tempfile

import pytest

from benchmarks.secure_memory.canonical import canonical_sha256
from benchmarks.secure_memory.models import ModelRequest
from benchmarks.secure_memory.substrate.broker import (
    BrokerDenied,
    BrokerState,
    ProviderCapabilityRecord,
    ProviderReply,
    ProviderRequestShape,
    ProviderBroker,
    ProviderTransportFailure,
    CampaignCapabilityAuthority,
    provision_secret_descriptor,
    read_authorized_secret_fd,
)
from tests.secure_memory.test_budget_ledger import SHA, _ledger


class FakeTransport:
    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply or ProviderReply(raw_usage={"input_tokens": 12, "output_tokens": 3}, output_text="ok", first_stream_ns=1, first_content_ns=2)
    def send(self, *, base_url, method, endpoint, body, api_key, allow_redirects, tls_verified):
        self.calls.append((method, base_url, endpoint, body, api_key, allow_redirects, tls_verified))
        return self.reply


def _request(*, lease=None, **overrides):
    values = dict(schema_version="secure-memory-model-request/v1", request_id="r1", campaign_id="campaign",
                  lease_sha256=SHA, ticket_id="ticket-1", request_class="main",
                  provider_base_url="https://apihub.agnes-ai.com/v1", provider_model="agnes-2.5-pro",
                  runtime="agentteams", messages=({"role": "user", "content": "hello"},),
                  max_input_tokens=10000, max_output_tokens=1500, temperature=0, top_p=1,
                  stream=True, tools=())
    values.update(overrides)
    if lease is not None:
        values["lease_sha256"] = lease.core_sha256
    return ModelRequest(**values)


def _capability(**overrides):
    values = dict(state=BrokerState.QUALIFIED, campaign_id="campaign", project_id="official-calibration-project",
                  base_url="https://apihub.agnes-ai.com/v1", endpoint="/chat/completions", method="POST",
                  body_shape=ProviderRequestShape.CHAT_COMPLETIONS, model="agnes-2.5-pro",
                  hard_output_limit=True, role_attribution=True, authoritative_usage=True,
                  streaming_semantics=True, zero_background_calls=True, calibrated_positive_error=0, temperature_present=True, top_p_present=True,
                  matrix_cases=("basic_nonstream_body", "stream_first_content", "tool_call_id", "tool_result_continuation", "hard_output_boundary", "context_overlimit_refusal", "authoritative_total_usage", "cached_input_subset", "reasoning_output_subset", "429_retry", "5xx_retry", "timeout", "redirect", "tls", "multi_role_attribution", "idle_window_zero_background_call"),
                  matrix_digest="", issuer_id="capability-control", key_id="capability-key", issue_sequence=1,
                  expires_at_sequence=10, record_sha256="", signature_base64="sig")
    values.update(overrides)
    values["matrix_digest"] = canonical_sha256("agentteams-capability-matrix", values["matrix_cases"])
    core = {key: value for key, value in values.items() if key not in {"record_sha256", "signature_base64"}}
    values["record_sha256"] = canonical_sha256("provider-capability-record", core)
    return ProviderCapabilityRecord(**values)


def _authority(**overrides):
    return CampaignCapabilityAuthority(_capability(**overrides), signature_verifier=lambda _value: True,
                                       current_sequence=lambda: 1, expected_campaign_id="campaign", expected_project_id="official-calibration-project", expected_issuer_id="capability-control", expected_key_id="capability-key")


def test_locked_capability_rejects_before_transport():
    with pytest.raises(BrokerDenied, match="capability_signature"):
        _authority(state=BrokerState.LOCKED)


def test_exact_qualified_request_is_forwarded_unchanged_with_terminal_usage():
    ledger, lease = _ledger()
    transport = FakeTransport()
    broker = ProviderBroker(ledger=ledger, capability_authority=_authority(), transport=transport,
                            signature_verifier=lambda _value: True, secret_fd=None)
    response = broker.dispatch(_request(lease=lease), lease=lease, requester_role="Worker")
    method, base_url, endpoint, body, _secret, redirects, tls = transport.calls[0]
    assert (method, base_url, endpoint, redirects, tls) == ("POST", "https://apihub.agnes-ai.com/v1", "/chat/completions", False, True)
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
    broker = ProviderBroker(ledger=ledger, capability_authority=_authority(), transport=transport,
                            signature_verifier=lambda _value: False, secret_fd=None)
    with pytest.raises(BrokerDenied, match="signature"):
        broker.dispatch(_request(lease=lease), lease=lease, requester_role="Worker")
    assert not transport.calls
    broker = ProviderBroker(ledger=ledger, capability_authority=_authority(endpoint="/other"), transport=transport,
                            signature_verifier=lambda _value: True, secret_fd=None)
    with pytest.raises(BrokerDenied, match="capability"):
        broker.dispatch(_request(lease=lease), lease=lease, requester_role="Worker")


def test_transport_failure_retains_reservation_and_sanitizes_error():
    ledger, lease = _ledger()
    class BrokenTransport(FakeTransport):
        def send(self, **kwargs):
            raise RuntimeError("api-key=definitely-not-logged")
    broker = ProviderBroker(ledger=ledger, capability_authority=_authority(), transport=BrokenTransport(),
                            signature_verifier=lambda _value: True, secret_fd=None)
    with pytest.raises(BrokerDenied, match="provider_failure") as error:
        broker.dispatch(_request(lease=lease), lease=lease, requester_role="Worker")
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


def test_provisioner_opens_a_temp_secret_once_with_bound_inode_handoff():
    fd, path = tempfile.mkstemp()
    os.write(fd, b"fake")
    os.close(fd)
    calls = []
    def opener(value, flags):
        calls.append((value, flags))
        return os.open(value, flags)
    handoff = provision_secret_descriptor(path, expected_uid=os.getuid(), opener=opener)
    try:
        assert len(calls) == 1
        assert calls[0][1] & os.O_NOFOLLOW
        assert read_authorized_secret_fd(handoff.fd, expected_uid=os.getuid(),
                                        expected_device=handoff.device, expected_inode=handoff.inode) == b"fake"
    finally:
        os.close(handoff.fd)
        os.unlink(path)


def test_request_lease_digest_usage_breach_and_null_timestamps_freeze_campaign():
    ledger, lease = _ledger()
    bad = _request(lease_sha256="b" * 64)
    transport = FakeTransport(ProviderReply(raw_usage={"input_tokens": 20_000, "output_tokens": 1}, output_text="x"))
    broker = ProviderBroker(ledger=ledger, capability_authority=_authority(), transport=transport,
                            signature_verifier=lambda _value: True, secret_fd=None)
    with pytest.raises(BrokerDenied, match="lease_digest"):
        broker.dispatch(bad, lease=lease, requester_role="Worker")
    with pytest.raises(BrokerDenied, match="authoritative_usage_invalid") as error:
        broker.dispatch(_request(lease=lease), lease=lease, requester_role="Worker")
    assert error.value.__cause__ is None
    with pytest.raises(BrokerDenied, match="capability_frozen"):
        broker.dispatch(_request(), lease=lease, requester_role="Worker")


def test_provider_null_timestamps_remain_null_and_base_url_is_exact():
    ledger, lease = _ledger()
    reply = ProviderReply(raw_usage={"input_tokens": 1, "output_tokens": 1}, output_text="x")
    transport = FakeTransport(reply)
    broker = ProviderBroker(ledger=ledger, capability_authority=_authority(), transport=transport,
                            signature_verifier=lambda _value: True, secret_fd=None)
    response = broker.dispatch(_request(lease=lease), lease=lease, requester_role="Worker")
    assert response.first_stream_ns is None
    assert response.first_content_ns is None
    assert transport.calls[0][1] == "https://apihub.agnes-ai.com/v1"


def test_shared_capability_authority_freezes_every_broker_after_background_call():
    capability = _capability()
    authority = CampaignCapabilityAuthority(capability, signature_verifier=lambda _value: True, current_sequence=lambda: 1, expected_campaign_id="campaign", expected_project_id="official-calibration-project", expected_issuer_id="capability-control", expected_key_id="capability-key")
    ledger_one, lease_one = _ledger()
    ledger_two, lease_two = _ledger()
    one = ProviderBroker(ledger=ledger_one, capability_authority=authority, transport=FakeTransport(),
                         signature_verifier=lambda _value: True, secret_fd=None)
    two = ProviderBroker(ledger=ledger_two, capability_authority=authority, transport=FakeTransport(),
                         signature_verifier=lambda _value: True, secret_fd=None)
    authority.observe_unattributed_call()
    for broker, ledger, lease in ((one, ledger_one, lease_one), (two, ledger_two, lease_two)):
        with pytest.raises(BrokerDenied, match="capability_frozen"):
            broker.dispatch(_request(lease=lease), lease=lease, requester_role="Worker")


def test_optional_field_contract_rejects_non_none_value_when_omitted():
    ledger, lease = _ledger()
    broker = ProviderBroker(ledger=ledger, capability_authority=_authority(temperature_present=False), transport=FakeTransport(),
                            signature_verifier=lambda _value: True, secret_fd=None)
    with pytest.raises(BrokerDenied, match="qualified_request"):
        broker.dispatch(_request(lease=lease), lease=lease, requester_role="Worker")


def test_request_token_ceilings_must_equal_trusted_ticket_before_transport():
    ledger, lease = _ledger()
    transport = FakeTransport()
    broker = ProviderBroker(ledger=ledger, capability_authority=_authority(), transport=transport,
                            signature_verifier=lambda _value: True, secret_fd=None)
    with pytest.raises(BrokerDenied, match="qualified_request"):
        broker.dispatch(_request(lease=lease, max_input_tokens=9999), lease=lease, requester_role="Worker")
    assert not transport.calls


def test_transient_failure_is_sanitized_and_retains_original_without_untrusted_retry():
    ledger, lease = _ledger()
    class Transient(FakeTransport):
        def send(self, **kwargs):
            raise ProviderTransportFailure.timeout()
    broker = ProviderBroker(ledger=ledger, capability_authority=_authority(), transport=Transient(),
                            signature_verifier=lambda _value: True, secret_fd=None)
    with pytest.raises(BrokerDenied, match="provider_failure"):
        broker.dispatch(_request(lease=lease), lease=lease, requester_role="Worker")
    assert ledger.reservation_for("ticket-1").state.value == "RETAINED"
