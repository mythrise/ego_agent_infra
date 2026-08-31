"""Authenticated channel framing regression tests."""

import copy

import pytest

from benchmarks.secure_memory.substrate.channel import (
    ChannelCodec,
    ChannelKind,
    ChannelRejected,
    KeyProvisioner,
)


def _codec() -> ChannelCodec:
    return ChannelCodec(
        provisioner=KeyProvisioner.deterministic(b"channel-test-seed"),
        configuration_id="A",
        campaign_nonce="campaign-nonce",
        arm="A",
        allowed_methods={ChannelKind.CANDIDATE: {"candidate.propose"}},
    )


def _frame(codec: ChannelCodec, **overrides: object) -> bytes:
    values = {
        "channel": ChannelKind.CANDIDATE,
        "sender_role": "agentteams",
        "recipient_role": "broker",
        "direction": "request",
        "key_id": "candidate-request",
        "epoch": 1,
        "sequence": 1,
        "method": "candidate.propose",
        "idempotency_key": "proposal-1",
        "payload": {"proposal_id": "proposal-1"},
    }
    values.update(overrides)
    return codec.encode(**values)


def _mutate(frame: bytes, **changes: object) -> bytes:
    import json

    document = json.loads(frame)
    if "mac" in changes:
        document["mac"] = changes.pop("mac")
    document["envelope"].update(changes)
    document["declared_length"] = len(
        json.dumps(document["envelope"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


@pytest.mark.parametrize(
    ("name", "change", "reason"),
    [
        ("wrong_hmac", {"mac": "00" * 32}, "invalid_mac"),
        ("wrong_configuration", {"configuration_id": "B"}, "configuration_mismatch"),
        ("wrong_nonce", {"campaign_nonce": "other"}, "campaign_nonce_mismatch"),
        ("wrong_sender", {"sender_role": "workspace"}, "identity_mismatch"),
        ("wrong_recipient", {"recipient_role": "control"}, "identity_mismatch"),
        ("wrong_direction", {"direction": "response"}, "direction_mismatch"),
        ("wrong_key_id", {"key_id": "other"}, "key_mismatch"),
        ("unknown_method", {"method": "candidate.unknown"}, "unknown_method"),
    ],
)
def test_rejects_authenticated_field_tampering(name: str, change: dict[str, object], reason: str) -> None:
    codec = _codec()
    frame = _frame(codec)
    with pytest.raises(ChannelRejected, match=reason):
        codec.receive(_mutate(frame, **change))


def test_valid_frame_and_idempotent_exact_replay_returns_cached_receipt() -> None:
    codec = _codec()
    frame = _frame(codec)
    calls: list[dict[str, object]] = []

    def route(envelope: object) -> bytes:
        calls.append(copy.copy(envelope.payload))  # type: ignore[attr-defined]
        return b"durable-receipt"

    assert codec.receive(frame, route=route) == b"durable-receipt"
    assert codec.receive(frame, route=route) == b"durable-receipt"
    assert calls == [{"proposal_id": "proposal-1"}]


def test_replay_gap_and_reflection_fail_closed_and_new_epoch_recovers() -> None:
    codec = _codec()
    frame = _frame(codec)
    codec.receive(frame, route=lambda _: b"ok")
    with pytest.raises(ChannelRejected, match="sequence_reuse_with_different_bytes"):
        codec.receive(_frame(codec, payload={"proposal_id": "other"}), route=lambda _: b"bad")
    with pytest.raises(ChannelRejected, match="sequence_mismatch"):
        codec.receive(_frame(codec, sequence=3), route=lambda _: b"bad")
    with pytest.raises(ChannelRejected, match="direction_mismatch"):
        codec.receive(
            _frame(codec, sender_role="broker", recipient_role="agentteams", direction="response"),
            route=lambda _: b"bad",
        )
    assert codec.receive(
        _frame(codec, epoch=2, idempotency_key="proposal-epoch-2"), route=lambda _: b"new"
    ) == b"new"


@pytest.mark.parametrize(
    "frame",
    [
        b"{\"payload\":\"\\ud800\"}",
        b'{"sequence":1,"sequence":1}',
        b'{"declared_length":999,"payload":{}}',
        b"{} trailing",
        b"x" * (1024 * 1024 + 1),
    ],
)
def test_malformed_frames_are_rejected_before_window_advance(frame: bytes) -> None:
    with pytest.raises(ChannelRejected):
        _codec().receive(frame, route=lambda _: b"must-not-run")
