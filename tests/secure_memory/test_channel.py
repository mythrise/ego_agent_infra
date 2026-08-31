"""Authenticated channel framing regression tests."""

import json
import threading

import pytest

from benchmarks.secure_memory.substrate.channel import (
    ChannelCodec,
    ChannelKind,
    ChannelRejected,
    ChannelTrust,
    DurableReceipt,
    KeyProvisioner,
)


def _trust(**overrides: object) -> ChannelTrust:
    values = dict(
        configuration_id="A",
        sender_role="agentteams",
        recipient_role="broker",
        direction="request",
        key_id="candidate-a-request-e1",
        epoch=1,
    )
    values.update(overrides)
    return ChannelTrust(**values)


def _codec(**overrides: object) -> ChannelCodec:
    values = dict(
        provisioner=KeyProvisioner.deterministic(b"channel-test-seed"),
        campaign_nonce="campaign-nonce",
        trusted_epoch=_trust(),
        allowed_methods={ChannelKind.CANDIDATE: {"candidate.propose"}},
    )
    values.update(overrides)
    return ChannelCodec(**values)


def _frame(codec: ChannelCodec, **overrides: object) -> bytes:
    values = dict(
        channel=ChannelKind.CANDIDATE,
        sender_role="agentteams",
        recipient_role="broker",
        direction="request",
        key_id="candidate-a-request-e1",
        epoch=1,
        sequence=1,
        method="candidate.propose",
        idempotency_key="proposal-1",
        payload={"proposal_id": "proposal-1"},
    )
    values.update(overrides)
    return codec.encode(**values)


def _receipt(_envelope: object, digest: str) -> DurableReceipt:
    return DurableReceipt(
        request_frame_sha256=digest,
        durable_receipt_id="journal-1",
        receipt_frame=b'{"receipt":"ok"}',
        installed=True,
    )


def test_trusted_epoch_key_identity_and_receipts_are_epoch_scoped() -> None:
    first = _codec()
    with pytest.raises(ChannelRejected, match="epoch_mismatch"):
        first.receive(_frame(first, epoch=999), route=_receipt)
    epoch_two = _trust(epoch=2, key_id="candidate-a-request-e2")
    second = _codec(trusted_epoch=epoch_two)
    frame = _frame(second, epoch=2, key_id="candidate-a-request-e2", idempotency_key="same")
    assert second.receive(frame, route=_receipt) == b'{"receipt":"ok"}'
    assert _codec().key_for_frame(_frame(_codec())) != second.key_for_frame(frame)


def test_concurrent_identical_delivery_routes_once_and_waits_for_receipt() -> None:
    codec = _codec()
    frame = _frame(codec)
    calls = []
    entered = threading.Event()
    release = threading.Event()

    def route(_envelope: object, digest: str) -> DurableReceipt:
        calls.append(digest)
        entered.set()
        release.wait(timeout=2)
        return _receipt(_envelope, digest)

    results = []
    first = threading.Thread(target=lambda: results.append(codec.receive(frame, route=route)))
    second = threading.Thread(target=lambda: results.append(codec.receive(frame, route=route)))
    first.start()
    assert entered.wait(timeout=2)
    second.start()
    release.set()
    first.join(timeout=3)
    second.join(timeout=3)
    assert results == [b'{"receipt":"ok"}', b'{"receipt":"ok"}']
    assert len(calls) == 1


def test_configuration_is_the_only_arm_and_semantic_hook_checks_payload_digest() -> None:
    with pytest.raises(TypeError):
        _codec(arm="B")
    from benchmarks.secure_memory.manifest import validate_wire_document
    from benchmarks.secure_memory.canonical import canonical_bytes, parse_json_bytes

    frame = _frame(_codec())
    envelope = parse_json_bytes(frame)["envelope"]
    envelope["payload_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="payload"):
        validate_wire_document("channel-envelope-v2.schema.json", canonical_bytes(envelope))


def test_frame_grammar_and_durable_receipt_validation_leave_window_retryable() -> None:
    codec = _codec()
    frame = _frame(codec)
    pretty = json.dumps(json.loads(frame), indent=2).encode()
    with pytest.raises(ChannelRejected, match="noncanonical_frame"):
        codec.receive(pretty, route=_receipt)
    bad = [
        DurableReceipt(
            request_frame_sha256=codec.frame_sha256(frame),
            durable_receipt_id="x",
            receipt_frame=b"",
            installed=True,
        ),
        DurableReceipt(
            request_frame_sha256="0" * 64,
            durable_receipt_id="x",
            receipt_frame=b"{}",
            installed=True,
        ),
        DurableReceipt(
            request_frame_sha256=codec.frame_sha256(frame),
            durable_receipt_id="x",
            receipt_frame=b"{}",
            installed=False,
        ),
    ]
    for receipt in bad:
        with pytest.raises(ChannelRejected):
            codec.receive(frame, route=lambda _e, _d, result=receipt: result)
    assert codec.receive(frame, route=_receipt) == b'{"receipt":"ok"}'


@pytest.mark.parametrize(
    "frame",
    [
        b'{"envelope":{}}',
        b'{"envelope":{},"envelope":{}}',
        b"{} trailing",
        b"x" * (1024 * 1024 + 1),
    ],
)
def test_invalid_frames_leave_sequence_one_available(frame: bytes) -> None:
    codec = _codec()
    with pytest.raises(ChannelRejected):
        codec.receive(frame, route=_receipt)
    assert codec.receive(_frame(codec), route=_receipt) == b'{"receipt":"ok"}'
