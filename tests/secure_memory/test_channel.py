"""Authenticated channel framing regression tests."""

import json
import threading

import pytest

from benchmarks.secure_memory.substrate.channel import (
    ChannelCodec,
    ChannelKind,
    ChannelRejected,
    ChannelTrust,
    InMemoryReceiptStore,
    KeyProvisioner,
    PendingReceipt,
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
        key_id=codec.material.key_id,
        epoch=codec.material.epoch,
        sequence=1,
        method="candidate.propose",
        idempotency_key="proposal-1",
        payload={"proposal_id": "proposal-1"},
    )
    values.update(overrides)
    return codec.encode(**values)


def _receipt(_envelope: object) -> PendingReceipt:
    return PendingReceipt(receipt_payload={"receipt": "ok"})


def test_trusted_epoch_key_identity_and_receipts_are_epoch_scoped() -> None:
    first = _codec()
    with pytest.raises(ChannelRejected, match="epoch_mismatch"):
        first.receive(_frame(first, epoch=999), route=_receipt)
    epoch_two = _trust(epoch=2, key_id="candidate-a-request-e2")
    second = _codec(trusted_epoch=epoch_two)
    frame = _frame(second, epoch=2, idempotency_key="same")
    assert second.receive(frame, route=_receipt) == b'{"receipt":"ok"}'
    assert _codec().key_for_frame(_frame(_codec())) != second.key_for_frame(frame)


def test_concurrent_identical_delivery_routes_once_and_waits_for_receipt() -> None:
    codec = _codec()
    frame = _frame(codec)
    calls = []
    entered = threading.Event()
    release = threading.Event()

    def route(_envelope: object) -> PendingReceipt:
        calls.append(1)
        entered.set()
        release.wait(timeout=2)
        return _receipt(_envelope)

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
    bad = [PendingReceipt(receipt_payload={}), PendingReceipt(receipt_payload=1)]
    for receipt in bad:
        with pytest.raises(ChannelRejected):
            codec.receive(frame, route=lambda _e, result=receipt: result)
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


def test_provisioned_keys_are_unique_and_same_sequence_different_bytes_never_route_twice() -> None:
    provisioner = KeyProvisioner.deterministic(b"channel-test-seed")
    materials = [
        provisioner.provision(
            campaign_nonce="campaign-nonce",
            channel=ChannelKind.CANDIDATE,
            configuration_id=value,
            sender_role="agentteams",
            recipient_role="broker",
            direction="request",
            epoch=epoch,
        )
        for value, epoch in (("A", 1), ("B", 1), ("A", 2))
    ]
    assert len({item.key_id for item in materials}) == len(materials)
    assert len({item.secret for item in materials}) == len(materials)
    codec = ChannelCodec(
        material=materials[0],
        allowed_methods={ChannelKind.CANDIDATE: {"candidate.propose"}},
        receipt_store=InMemoryReceiptStore(),
    )
    first = _frame(codec, payload={"proposal_id": "first"}, idempotency_key="first")
    second = _frame(codec, payload={"proposal_id": "second"}, idempotency_key="second")
    calls = []

    def route(_envelope: object) -> PendingReceipt:
        calls.append(1)
        return PendingReceipt(receipt_payload={"receipt": "ok"})

    results = []
    errors = []

    def deliver(value: bytes) -> None:
        try:
            results.append(codec.receive(value, route=route))
        except ChannelRejected as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=lambda value=value: deliver(value)) for value in (first, second)
    ]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert len(calls) == 1
    assert len(results) == 1
    assert len(errors) == 1


def test_store_owned_installation_reconciles_and_rejects_scalar_pending_payload() -> None:
    material = KeyProvisioner.deterministic(b"channel-test-seed").provision(
        campaign_nonce="campaign-nonce",
        channel=ChannelKind.CANDIDATE,
        configuration_id="A",
        sender_role="agentteams",
        recipient_role="broker",
        direction="request",
        epoch=1,
    )
    store = InMemoryReceiptStore()
    codec = ChannelCodec(
        material=material,
        allowed_methods={ChannelKind.CANDIDATE: {"candidate.propose"}},
        receipt_store=store,
    )
    frame = _frame(codec)
    with pytest.raises(ChannelRejected):
        codec.receive(frame, route=lambda _e: PendingReceipt(receipt_payload=1))
    assert (
        codec.receive(frame, route=lambda _e: PendingReceipt(receipt_payload={"ok": True}))
        == b'{"ok":true}'
    )


def test_historical_valid_digest_conflict_is_not_store_corruption() -> None:
    store = InMemoryReceiptStore()
    codec = _codec(receipt_store=store)
    one = _frame(codec, sequence=1, idempotency_key="one")
    two = _frame(codec, sequence=2, idempotency_key="two")
    codec.receive(one, route=lambda _e: PendingReceipt(receipt_payload={"seq": 1}))
    codec.receive(two, route=lambda _e: PendingReceipt(receipt_payload={"seq": 2}))
    changed = _frame(
        codec, sequence=1, idempotency_key="changed", payload={"proposal_id": "changed"}
    )
    with pytest.raises(ChannelRejected, match="sequence_reuse_with_different_bytes"):
        _codec(receipt_store=store).receive(
            changed, route=lambda _e: (_ for _ in ()).throw(AssertionError("routed"))
        )
