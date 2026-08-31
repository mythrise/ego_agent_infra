"""Canonical authenticated framing with trusted epoch and durable receipts."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Literal, Mapping, Optional, Set, Tuple

from pydantic import Field, ValidationError

from ..canonical import canonical_bytes, canonical_sha256, parse_json_bytes, validate_sha256_digest
from ..models import Digest, MeasuredConfigurationId, StrictModel

MAX_FRAME_BYTES = 1024 * 1024
Role = Literal["agentteams", "workspace", "control", "evaluator", "broker", "controller"]
Direction = Literal["request", "response", "receipt"]


class ChannelRejected(ValueError):
    pass


class ChannelKind(str, Enum):
    MODEL = "model"
    CANDIDATE = "candidate"
    AGENTTEAMS_CONTROL = "agentteams-control"
    WORKSPACE_EFFECT = "workspace-effect"
    CONTROL_RESULT = "control-result"
    EVALUATOR = "evaluator"


class ChannelEnvelope(StrictModel):
    schema_version: Literal["secure-memory-channel/v2"]
    channel: ChannelKind
    configuration_id: MeasuredConfigurationId
    sender_role: Role
    recipient_role: Role
    direction: Direction
    key_id: str = Field(min_length=1)
    campaign_nonce: str = Field(min_length=1)
    epoch: int = Field(ge=1)
    sequence: int = Field(ge=1)
    method: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    payload_sha256: Digest
    payload: Dict[str, Any]


@dataclass(frozen=True)
class ChannelTrust:
    configuration_id: str
    sender_role: Role
    recipient_role: Role
    direction: Direction
    key_id: str
    epoch: int

    def __post_init__(self) -> None:
        MeasuredConfigurationId(self.configuration_id)
        if not self.key_id or self.epoch < 1:
            raise ValueError("trusted channel epoch requires key_id and positive epoch")


@dataclass(frozen=True)
class DurableReceipt:
    request_frame_sha256: str
    durable_receipt_id: str
    receipt_frame: bytes
    installed: bool


@dataclass
class _Window:
    last_sequence: int = 0
    last_digest: Optional[str] = None
    last_receipt: Optional[DurableReceipt] = None


@dataclass(frozen=True)
class KeyProvisioner:
    seed: bytes

    @classmethod
    def deterministic(cls, seed: bytes) -> "KeyProvisioner":
        if not isinstance(seed, bytes) or len(seed) < 16:
            raise ValueError("deterministic test seed must contain at least 16 bytes")
        return cls(seed)

    @classmethod
    def random(cls) -> "KeyProvisioner":
        return cls(secrets.token_bytes(32))

    def key_for(self, *, channel: ChannelKind, trust: ChannelTrust) -> bytes:
        context = canonical_bytes(
            {
                "channel": channel.value,
                "configuration_id": trust.configuration_id,
                "sender_role": trust.sender_role,
                "recipient_role": trust.recipient_role,
                "direction": trust.direction,
                "key_id": trust.key_id,
                "epoch": trust.epoch,
            }
        )
        return hmac.new(
            self.seed, b"secure-memory-channel-key/v2\x00" + context, hashlib.sha256
        ).digest()


Route = Callable[[ChannelEnvelope, str], DurableReceipt]
WindowKey = Tuple[ChannelKind, str, str, str, str, str, int]
ReceiptKey = Tuple[ChannelKind, str, str, str, str, str, int, str]


class ChannelCodec:
    """Trusted receiver state.  The supplied ChannelTrust is the only epoch authority."""

    def __init__(
        self,
        *,
        provisioner: KeyProvisioner,
        campaign_nonce: str,
        trusted_epoch: ChannelTrust,
        allowed_methods: Mapping[ChannelKind, Set[str]],
    ) -> None:
        self._provisioner = provisioner
        self._campaign_nonce = campaign_nonce
        self._trust = trusted_epoch
        self._methods = {kind: frozenset(methods) for kind, methods in allowed_methods.items()}
        self._windows: Dict[WindowKey, _Window] = {}
        self._receipts: Dict[ReceiptKey, DurableReceipt] = {}
        self._inflight: Set[Tuple[WindowKey, int, str]] = set()
        self._condition = threading.Condition(threading.RLock())

    def encode(
        self,
        *,
        channel: ChannelKind,
        sender_role: Role,
        recipient_role: Role,
        direction: Direction,
        key_id: str,
        epoch: int,
        sequence: int,
        method: str,
        idempotency_key: str,
        payload: Dict[str, Any],
    ) -> bytes:
        envelope = ChannelEnvelope(
            schema_version="secure-memory-channel/v2",
            channel=channel,
            configuration_id=MeasuredConfigurationId(self._trust.configuration_id),
            sender_role=sender_role,
            recipient_role=recipient_role,
            direction=direction,
            key_id=key_id,
            campaign_nonce=self._campaign_nonce,
            epoch=epoch,
            sequence=sequence,
            method=method,
            idempotency_key=idempotency_key,
            payload_sha256=canonical_sha256("channel-payload", payload),
            payload=payload,
        )
        document = envelope.model_dump(mode="json")
        return canonical_bytes(
            {
                "declared_length": len(canonical_bytes(document)),
                "envelope": document,
                "mac": self._mac(envelope),
            }
        )

    def frame_sha256(self, frame: bytes) -> str:
        return _frame_sha256(frame)

    def key_for_frame(self, frame: bytes) -> bytes:
        envelope, _ = self._decode(frame)
        return self._provisioner.key_for(channel=envelope.channel, trust=self._trust_from(envelope))

    def receive(self, frame: bytes, *, route: Route) -> bytes:
        envelope, supplied = self._decode(frame)
        self._verify(envelope)
        if not hmac.compare_digest(supplied, self._mac(envelope)):
            raise ChannelRejected("invalid_mac")
        window_key = self._window_key(envelope)
        digest = _frame_sha256(frame)
        receipt_key = self._receipt_key(envelope)
        inflight_key = (window_key, envelope.sequence, digest)
        with self._condition:
            while inflight_key in self._inflight:
                self._condition.wait()
            window = self._windows.setdefault(window_key, _Window())
            if envelope.sequence == window.last_sequence:
                if window.last_digest == digest and window.last_receipt is not None:
                    return window.last_receipt.receipt_frame
                raise ChannelRejected("sequence_reuse_with_different_bytes")
            if envelope.sequence != window.last_sequence + 1:
                raise ChannelRejected("sequence_mismatch")
            if receipt_key in self._receipts:
                raise ChannelRejected("idempotency_reuse_with_different_bytes")
            self._inflight.add(inflight_key)
        try:
            result = route(envelope, digest)
            self._validate_receipt(result, digest)
        except Exception:
            with self._condition:
                self._inflight.discard(inflight_key)
                self._condition.notify_all()
            raise
        with self._condition:
            # State cannot have moved because an in-flight reservation owns this sequence.
            window = self._windows[window_key]
            self._receipts[receipt_key] = result
            window.last_sequence = envelope.sequence
            window.last_digest = digest
            window.last_receipt = result
            self._inflight.discard(inflight_key)
            self._condition.notify_all()
        return result.receipt_frame

    def _decode(self, frame: bytes) -> Tuple[ChannelEnvelope, str]:
        if not isinstance(frame, bytes):
            raise ChannelRejected("frame_must_be_bytes")
        if len(frame) > MAX_FRAME_BYTES:
            raise ChannelRejected("frame_too_large")
        try:
            document = parse_json_bytes(frame)
        except (TypeError, ValueError) as exc:
            raise ChannelRejected("invalid_frame") from exc
        if canonical_bytes(document) != frame:
            raise ChannelRejected("noncanonical_frame")
        if not isinstance(document, dict) or set(document) != {
            "declared_length",
            "envelope",
            "mac",
        }:
            raise ChannelRejected("invalid_frame")
        raw = document["envelope"]
        if (
            isinstance(document["declared_length"], bool)
            or not isinstance(document["declared_length"], int)
            or not isinstance(raw, dict)
            or document["declared_length"] != len(canonical_bytes(raw))
        ):
            raise ChannelRejected("declared_length_mismatch")
        try:
            validate_sha256_digest(document["mac"])
            envelope = ChannelEnvelope.model_validate(raw)
        except (TypeError, ValueError, ValidationError) as exc:
            raise ChannelRejected("invalid_envelope") from exc
        return envelope, document["mac"]

    def _verify(self, envelope: ChannelEnvelope) -> None:
        trust = self._trust
        if envelope.configuration_id.value != trust.configuration_id:
            raise ChannelRejected("configuration_mismatch")
        if envelope.epoch != trust.epoch:
            raise ChannelRejected("epoch_mismatch")
        if envelope.key_id != trust.key_id:
            raise ChannelRejected("key_mismatch")
        if envelope.campaign_nonce != self._campaign_nonce:
            raise ChannelRejected("campaign_nonce_mismatch")
        if envelope.direction != trust.direction:
            raise ChannelRejected("direction_mismatch")
        if (
            envelope.sender_role != trust.sender_role
            or envelope.recipient_role != trust.recipient_role
        ):
            raise ChannelRejected("identity_mismatch")
        if envelope.method not in self._methods.get(envelope.channel, frozenset()):
            raise ChannelRejected("unknown_method")
        if envelope.payload_sha256 != canonical_sha256("channel-payload", envelope.payload):
            raise ChannelRejected("payload_digest_mismatch")

    def _mac(self, envelope: ChannelEnvelope) -> str:
        trust = self._trust_from(envelope)
        protected = envelope.model_dump(mode="json")
        protected.pop("payload_sha256")
        return hmac.new(
            self._provisioner.key_for(channel=envelope.channel, trust=trust),
            canonical_bytes(protected),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _trust_from(envelope: ChannelEnvelope) -> ChannelTrust:
        return ChannelTrust(
            configuration_id=envelope.configuration_id.value,
            sender_role=envelope.sender_role,
            recipient_role=envelope.recipient_role,
            direction=envelope.direction,
            key_id=envelope.key_id,
            epoch=envelope.epoch,
        )

    @staticmethod
    def _window_key(envelope: ChannelEnvelope) -> WindowKey:
        return (
            envelope.channel,
            envelope.configuration_id.value,
            envelope.sender_role,
            envelope.recipient_role,
            envelope.direction,
            envelope.key_id,
            envelope.epoch,
        )

    @staticmethod
    def _receipt_key(envelope: ChannelEnvelope) -> ReceiptKey:
        return ChannelCodec._window_key(envelope) + (envelope.idempotency_key,)

    @staticmethod
    def _validate_receipt(receipt: DurableReceipt, digest: str) -> None:
        if (
            not isinstance(receipt, DurableReceipt)
            or not receipt.installed
            or not receipt.durable_receipt_id
            or not receipt.receipt_frame
            or receipt.request_frame_sha256 != digest
        ):
            raise ChannelRejected("invalid_durable_receipt")
        try:
            decoded = parse_json_bytes(receipt.receipt_frame)
        except (TypeError, ValueError) as exc:
            raise ChannelRejected("invalid_durable_receipt") from exc
        if canonical_bytes(decoded) != receipt.receipt_frame:
            raise ChannelRejected("invalid_durable_receipt")


def _frame_sha256(frame: bytes) -> str:
    return hashlib.sha256(b"secure-memory-channel-frame/v2\x00" + frame).hexdigest()
