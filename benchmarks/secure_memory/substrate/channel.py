"""Authenticated, replay-safe framing for campaign-local transports.

The wire format is canonical JSON with a length-delimited envelope and an
HMAC-SHA256 over every routing attribute and the canonical payload.  It is
deliberately transport-neutral: callers provide and receive bytes only.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
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
    """A framing, authentication, or replay check failed closed."""


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
    arm: MeasuredConfigurationId
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
class CachedReceipt:
    request_frame_sha256: str
    receipt_frame: bytes


class ReceiveWindow:
    """One strict receive sequence for exactly one direction/key/epoch tuple."""

    def __init__(self) -> None:
        self.last_sequence = 0
        self.last_frame_sha256: Optional[str] = None
        self.last_receipt: Optional[CachedReceipt] = None

    def accept(self, frame: bytes, envelope: ChannelEnvelope) -> Optional[CachedReceipt]:
        frame_digest = _frame_sha256(frame)
        if envelope.sequence == self.last_sequence:
            if frame_digest == self.last_frame_sha256:
                return self.last_receipt
            raise ChannelRejected("sequence_reuse_with_different_bytes")
        if envelope.sequence != self.last_sequence + 1:
            raise ChannelRejected("sequence_mismatch")
        return None

    def commit(self, frame: bytes, envelope: ChannelEnvelope, receipt: CachedReceipt) -> None:
        self.last_sequence = envelope.sequence
        self.last_frame_sha256 = _frame_sha256(frame)
        self.last_receipt = receipt


@dataclass(frozen=True)
class KeyProvisioner:
    """Campaign-local key schedule used by offline fixtures and trusted endpoints.

    A production provisioner creates this object in trusted control and shares
    each derived key only with its sender and receiver.  This value object does
    not serialize or log key material.
    """

    seed: bytes

    @classmethod
    def deterministic(cls, seed: bytes) -> "KeyProvisioner":
        if not isinstance(seed, bytes) or len(seed) < 16:
            raise ValueError("deterministic test seed must contain at least 16 bytes")
        return cls(seed=seed)

    @classmethod
    def random(cls) -> "KeyProvisioner":
        return cls(seed=secrets.token_bytes(32))

    def key_for(
        self,
        *,
        channel: ChannelKind,
        arm: MeasuredConfigurationId,
        sender_role: Role,
        recipient_role: Role,
        direction: Direction,
        key_id: str,
    ) -> bytes:
        context = canonical_bytes(
            {
                "arm": arm.value,
                "channel": channel.value,
                "direction": direction,
                "key_id": key_id,
                "recipient_role": recipient_role,
                "sender_role": sender_role,
            }
        )
        return hmac.new(self.seed, b"secure-memory-channel-key/v2\x00" + context, hashlib.sha256).digest()


Route = Callable[[ChannelEnvelope], bytes]
WindowKey = Tuple[ChannelKind, MeasuredConfigurationId, str, str, str, str, int]
IdempotencyKey = Tuple[ChannelKind, MeasuredConfigurationId, str, str, str, str, str]


class ChannelCodec:
    """Encode frames and accept only authenticated in-order inbound frames."""

    def __init__(
        self,
        *,
        provisioner: KeyProvisioner,
        configuration_id: str,
        campaign_nonce: str,
        arm: str,
        allowed_methods: Mapping[ChannelKind, Set[str]],
        sender_role: Role = "agentteams",
        recipient_role: Role = "broker",
        direction: Direction = "request",
    ) -> None:
        self._provisioner = provisioner
        self._configuration_id = MeasuredConfigurationId(configuration_id)
        self._campaign_nonce = campaign_nonce
        self._arm = MeasuredConfigurationId(arm)
        self._allowed_methods = {kind: frozenset(methods) for kind, methods in allowed_methods.items()}
        self._sender_role = sender_role
        self._recipient_role = recipient_role
        self._direction = direction
        self._windows: Dict[WindowKey, ReceiveWindow] = {}
        self._receipts: Dict[IdempotencyKey, CachedReceipt] = {}

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
            configuration_id=self._configuration_id,
            arm=self._arm,
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
        envelope_document = envelope.model_dump(mode="json")
        return canonical_bytes(
            {
                "declared_length": len(canonical_bytes(envelope_document)),
                "envelope": envelope_document,
                "mac": self._mac(envelope),
            }
        )

    def receive(self, frame: bytes, *, route: Optional[Route] = None) -> bytes:
        envelope, supplied_mac = self._decode(frame)
        self._verify_identity_and_method(envelope)
        if envelope.payload_sha256 != canonical_sha256("channel-payload", envelope.payload):
            raise ChannelRejected("payload_digest_mismatch")
        expected_mac = self._mac(envelope)
        if not hmac.compare_digest(supplied_mac, expected_mac):
            raise ChannelRejected("invalid_mac")

        window_key = self._window_key(envelope)
        receipt_key = self._receipt_key(envelope)
        request_digest = _frame_sha256(frame)
        window = self._windows.setdefault(window_key, ReceiveWindow())
        replay = window.accept(frame, envelope)
        if replay is not None:
            return replay.receipt_frame

        prior = self._receipts.get(receipt_key)
        if prior is not None:
            if prior.request_frame_sha256 == request_digest:
                raise ChannelRejected("idempotency_replay_outside_receive_window")
            raise ChannelRejected("idempotency_reuse_with_different_bytes")

        if route is None:
            raise ChannelRejected("missing_durable_receipt_route")
        try:
            receipt_frame = route(envelope)
        except Exception:
            raise
        if not isinstance(receipt_frame, bytes):
            raise ChannelRejected("invalid_durable_receipt")
        cached = CachedReceipt(request_frame_sha256=request_digest, receipt_frame=receipt_frame)
        self._receipts[receipt_key] = cached
        window.commit(frame, envelope, cached)
        return receipt_frame

    def _decode(self, frame: bytes) -> Tuple[ChannelEnvelope, str]:
        if not isinstance(frame, bytes):
            raise ChannelRejected("frame_must_be_bytes")
        if len(frame) > MAX_FRAME_BYTES:
            raise ChannelRejected("frame_too_large")
        try:
            document = parse_json_bytes(frame)
        except (TypeError, ValueError) as exc:
            raise ChannelRejected("invalid_frame") from exc
        if not isinstance(document, dict) or set(document) != {"declared_length", "envelope", "mac"}:
            raise ChannelRejected("invalid_frame")
        declared_length = document["declared_length"]
        raw_envelope = document["envelope"]
        supplied_mac = document["mac"]
        if isinstance(declared_length, bool) or not isinstance(declared_length, int):
            raise ChannelRejected("declared_length_mismatch")
        if not isinstance(raw_envelope, dict) or declared_length != len(canonical_bytes(raw_envelope)):
            raise ChannelRejected("declared_length_mismatch")
        if not isinstance(supplied_mac, str):
            raise ChannelRejected("invalid_mac")
        try:
            validate_sha256_digest(supplied_mac)
            envelope = ChannelEnvelope.model_validate(raw_envelope)
        except (TypeError, ValidationError, ValueError) as exc:
            raise ChannelRejected("invalid_envelope") from exc
        return envelope, supplied_mac

    def _verify_identity_and_method(self, envelope: ChannelEnvelope) -> None:
        if envelope.configuration_id != self._configuration_id or envelope.arm != self._arm:
            raise ChannelRejected("configuration_mismatch")
        if envelope.campaign_nonce != self._campaign_nonce:
            raise ChannelRejected("campaign_nonce_mismatch")
        if envelope.direction != self._direction:
            raise ChannelRejected("direction_mismatch")
        if envelope.sender_role != self._sender_role or envelope.recipient_role != self._recipient_role:
            raise ChannelRejected("identity_mismatch")
        if envelope.key_id != "%s-%s" % (envelope.channel.value, envelope.direction):
            raise ChannelRejected("key_mismatch")
        if envelope.method not in self._allowed_methods.get(envelope.channel, frozenset()):
            raise ChannelRejected("unknown_method")

    def _mac(self, envelope: ChannelEnvelope) -> str:
        key = self._provisioner.key_for(
            channel=envelope.channel,
            arm=envelope.arm,
            sender_role=envelope.sender_role,
            recipient_role=envelope.recipient_role,
            direction=envelope.direction,
            key_id=envelope.key_id,
        )
        protected = {
            "arm": envelope.arm.value,
            "campaign_nonce": envelope.campaign_nonce,
            "channel": envelope.channel.value,
            "configuration_id": envelope.configuration_id.value,
            "direction": envelope.direction,
            "epoch": envelope.epoch,
            "idempotency_key": envelope.idempotency_key,
            "key_id": envelope.key_id,
            "method": envelope.method,
            "payload": envelope.payload,
            "recipient_role": envelope.recipient_role,
            "schema_version": envelope.schema_version,
            "sender_role": envelope.sender_role,
            "sequence": envelope.sequence,
        }
        return hmac.new(key, canonical_bytes(protected), hashlib.sha256).hexdigest()

    @staticmethod
    def _window_key(envelope: ChannelEnvelope) -> WindowKey:
        return (
            envelope.channel,
            envelope.arm,
            envelope.sender_role,
            envelope.recipient_role,
            envelope.direction,
            envelope.key_id,
            envelope.epoch,
        )

    @staticmethod
    def _receipt_key(envelope: ChannelEnvelope) -> IdempotencyKey:
        return (
            envelope.channel,
            envelope.arm,
            envelope.sender_role,
            envelope.recipient_role,
            envelope.direction,
            envelope.key_id,
            envelope.idempotency_key,
        )


def _frame_sha256(frame: bytes) -> str:
    return hashlib.sha256(b"secure-memory-channel-frame/v2\x00" + frame).hexdigest()
