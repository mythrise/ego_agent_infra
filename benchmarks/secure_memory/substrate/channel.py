"""Provisioned canonical channel framing and store-owned durable receipts."""

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
class ProvisionedChannelKey:
    campaign_nonce: str
    channel: ChannelKind
    configuration_id: str
    sender_role: Role
    recipient_role: Role
    direction: Direction
    epoch: int
    key_id: str
    secret: bytes


@dataclass(frozen=True)
class ChannelTrust:  # legacy fixture descriptor; production codecs take ProvisionedChannelKey.
    configuration_id: str
    sender_role: Role
    recipient_role: Role
    direction: Direction
    key_id: str
    epoch: int


@dataclass(frozen=True)
class PendingReceipt:
    receipt_payload: Mapping[str, Any]


@dataclass(frozen=True)
class DurableReceipt:  # retained as data type only; never accepted as a route result.
    request_frame_sha256: str
    durable_receipt_id: str
    receipt_frame: bytes
    installed: bool


@dataclass(frozen=True)
class InstalledReceipt:
    request_frame_sha256: str
    durable_receipt_id: str
    receipt_frame: bytes
    identity: Tuple[object, ...]


class KeyProvisioner:
    def __init__(self, seed: bytes):
        self.seed = seed

    @classmethod
    def deterministic(cls, seed: bytes) -> "KeyProvisioner":
        if not isinstance(seed, bytes) or len(seed) < 16:
            raise ValueError("seed")
        return cls(seed)

    @classmethod
    def random(cls) -> "KeyProvisioner":
        return cls(secrets.token_bytes(32))

    def provision(
        self,
        *,
        campaign_nonce: str,
        channel: ChannelKind,
        configuration_id: str,
        sender_role: Role,
        recipient_role: Role,
        direction: Direction,
        epoch: int,
    ) -> ProvisionedChannelKey:
        MeasuredConfigurationId(configuration_id)
        context = {
            "campaign_nonce": campaign_nonce,
            "channel": channel.value,
            "configuration_id": configuration_id,
            "sender_role": sender_role,
            "recipient_role": recipient_role,
            "direction": direction,
            "epoch": epoch,
        }
        secret = hmac.new(
            self.seed, b"secure-memory-key/v3\0" + canonical_bytes(context), hashlib.sha256
        ).digest()
        key_id = hashlib.sha256(b"secure-memory-key-id/v3\0" + secret).hexdigest()
        return ProvisionedChannelKey(
            campaign_nonce=campaign_nonce,
            channel=channel,
            configuration_id=configuration_id,
            sender_role=sender_role,
            recipient_role=recipient_role,
            direction=direction,
            epoch=epoch,
            key_id=key_id,
            secret=secret,
        )


class InMemoryReceiptStore:
    def __init__(self) -> None:
        self._records: Dict[Tuple[object, ...], InstalledReceipt] = {}
        self._lock = threading.RLock()

    def install_or_get(
        self, identity: Tuple[object, ...], digest: str, pending: PendingReceipt
    ) -> InstalledReceipt:
        if (
            not isinstance(pending, PendingReceipt)
            or not isinstance(pending.receipt_payload, Mapping)
            or not pending.receipt_payload
        ):
            raise ChannelRejected("invalid_pending_receipt")
        frame = canonical_bytes(dict(pending.receipt_payload))
        if not frame or not isinstance(parse_json_bytes(frame), dict):
            raise ChannelRejected("invalid_pending_receipt")
        with self._lock:
            current = self._records.get(identity)
            if current is not None:
                if current.request_frame_sha256 != digest:
                    raise ChannelRejected("receipt_store_mismatch")
                return current
            receipt_id = hashlib.sha256(
                b"receipt/v1\0" + canonical_bytes(identity) + digest.encode()
            ).hexdigest()
            result = InstalledReceipt(digest, receipt_id, frame, identity)
            self._records[identity] = result
            return result

    def lookup(self, identity: Tuple[object, ...], digest: str) -> Optional[InstalledReceipt]:
        with self._lock:
            result = self._records.get(identity)
            return result if result and result.request_frame_sha256 == digest else None


Route = Callable[[ChannelEnvelope], PendingReceipt]
WindowKey = Tuple[object, ...]


class ChannelCodec:
    def __init__(
        self,
        *,
        material: Optional[ProvisionedChannelKey] = None,
        allowed_methods: Mapping[ChannelKind, Set[str]],
        receipt_store: Optional[InMemoryReceiptStore] = None,
        provisioner: Optional[KeyProvisioner] = None,
        campaign_nonce: Optional[str] = None,
        trusted_epoch: Optional[ChannelTrust] = None,
    ) -> None:
        if material is None:
            if provisioner is None or campaign_nonce is None or trusted_epoch is None:
                raise TypeError("material is required")
            material = provisioner.provision(
                campaign_nonce=campaign_nonce,
                channel=ChannelKind.CANDIDATE,
                configuration_id=trusted_epoch.configuration_id,
                sender_role=trusted_epoch.sender_role,
                recipient_role=trusted_epoch.recipient_role,
                direction=trusted_epoch.direction,
                epoch=trusted_epoch.epoch,
            )
        self.material = material
        self._methods = {k: frozenset(v) for k, v in allowed_methods.items()}
        self.store = receipt_store or InMemoryReceiptStore()
        self._windows: Dict[WindowKey, Tuple[int, Optional[str], Optional[InstalledReceipt]]] = {}
        self._inflight: Dict[Tuple[WindowKey, int], str] = {}
        self._condition = threading.Condition(threading.RLock())

    def encode(
        self,
        *,
        channel: ChannelKind,
        sender_role: Role,
        recipient_role: Role,
        direction: Direction,
        key_id: Optional[str] = None,
        epoch: Optional[int] = None,
        sequence: int,
        method: str,
        idempotency_key: str,
        payload: Dict[str, Any],
    ) -> bytes:
        m = self.material
        env = ChannelEnvelope(
            schema_version="secure-memory-channel/v2",
            channel=channel,
            configuration_id=MeasuredConfigurationId(m.configuration_id),
            sender_role=sender_role,
            recipient_role=recipient_role,
            direction=direction,
            key_id=key_id or m.key_id,
            campaign_nonce=m.campaign_nonce,
            epoch=epoch or m.epoch,
            sequence=sequence,
            method=method,
            idempotency_key=idempotency_key,
            payload_sha256=canonical_sha256("channel-payload", payload),
            payload=payload,
        )
        body = env.model_dump(mode="json")
        return canonical_bytes(
            {"declared_length": len(canonical_bytes(body)), "envelope": body, "mac": self._mac(env)}
        )

    def frame_sha256(self, frame: bytes) -> str:
        return _digest(frame)

    def key_for_frame(self, frame: bytes) -> bytes:
        return self.material.secret

    def receive(self, frame: bytes, *, route: Route) -> bytes:
        env, mac = self._decode(frame)
        self._verify(env)
        if not hmac.compare_digest(mac, self._mac(env)):
            raise ChannelRejected("invalid_mac")
        key = self._window(env)
        digest = _digest(frame)
        reservation = (key, env.sequence)
        identity = key + (env.sequence,)
        with self._condition:
            while reservation in self._inflight:
                if self._inflight[reservation] != digest:
                    raise ChannelRejected("sequence_reuse_with_different_bytes")
                self._condition.wait()
            last, last_digest, last_receipt = self._windows.get(key, (0, None, None))
            if env.sequence == last:
                if last_digest == digest and last_receipt:
                    return last_receipt.receipt_frame
                raise ChannelRejected("sequence_reuse_with_different_bytes")
            if env.sequence != last + 1:
                raise ChannelRejected("sequence_mismatch")
            existing = self.store.lookup(identity, digest)
            if existing:
                self._windows[key] = (env.sequence, digest, existing)
                return existing.receipt_frame
            self._inflight[reservation] = digest
        try:
            pending = route(env)
            installed = self.store.install_or_get(identity, digest, pending)
            if (
                not installed.durable_receipt_id
                or installed.identity != identity
                or installed.request_frame_sha256 != digest
                or not installed.receipt_frame
                or canonical_bytes(parse_json_bytes(installed.receipt_frame))
                != installed.receipt_frame
            ):
                raise ChannelRejected("invalid_durable_receipt")
        except Exception:
            with self._condition:
                self._inflight.pop(reservation, None)
                self._condition.notify_all()
            raise
        with self._condition:
            last, last_digest, _ = self._windows.get(key, (0, None, None))
            if last not in (env.sequence - 1, env.sequence) or (
                last == env.sequence and last_digest != digest
            ):
                raise ChannelRejected("sequence_commit_conflict")
            self._windows[key] = (env.sequence, digest, installed)
            self._inflight.pop(reservation, None)
            self._condition.notify_all()
        return installed.receipt_frame

    def _decode(self, frame: bytes) -> Tuple[ChannelEnvelope, str]:
        if not isinstance(frame, bytes):
            raise ChannelRejected("frame_must_be_bytes")
        if len(frame) > MAX_FRAME_BYTES:
            raise ChannelRejected("frame_too_large")
        try:
            doc = parse_json_bytes(frame)
        except (TypeError, ValueError) as exc:
            raise ChannelRejected("invalid_frame") from exc
        if canonical_bytes(doc) != frame:
            raise ChannelRejected("noncanonical_frame")
        if not isinstance(doc, dict) or set(doc) != {"declared_length", "envelope", "mac"}:
            raise ChannelRejected("invalid_frame")
        raw = doc["envelope"]
        if not isinstance(raw, dict) or doc["declared_length"] != len(canonical_bytes(raw)):
            raise ChannelRejected("declared_length_mismatch")
        try:
            validate_sha256_digest(doc["mac"])
            env = ChannelEnvelope.model_validate(raw)
        except (TypeError, ValueError, ValidationError) as exc:
            raise ChannelRejected("invalid_envelope") from exc
        return env, doc["mac"]

    def _verify(self, e: ChannelEnvelope) -> None:
        m = self.material
        if e.channel != m.channel or e.configuration_id.value != m.configuration_id:
            raise ChannelRejected("configuration_mismatch")
        if e.epoch != m.epoch:
            raise ChannelRejected("epoch_mismatch")
        if (e.sender_role, e.recipient_role, e.direction, e.key_id) != (
            m.sender_role,
            m.recipient_role,
            m.direction,
            m.key_id,
        ):
            raise ChannelRejected("identity_mismatch")
        if e.campaign_nonce != m.campaign_nonce:
            raise ChannelRejected("campaign_nonce_mismatch")
        if e.method not in self._methods.get(e.channel, frozenset()):
            raise ChannelRejected("unknown_method")
        if e.payload_sha256 != canonical_sha256("channel-payload", e.payload):
            raise ChannelRejected("payload_digest_mismatch")

    def _mac(self, e: ChannelEnvelope) -> str:
        value = e.model_dump(mode="json")
        value.pop("payload_sha256")
        return hmac.new(self.material.secret, canonical_bytes(value), hashlib.sha256).hexdigest()

    @staticmethod
    def _window(e: ChannelEnvelope) -> WindowKey:
        return (
            e.channel,
            e.configuration_id.value,
            e.sender_role,
            e.recipient_role,
            e.direction,
            e.key_id,
            e.epoch,
        )


def _digest(frame: bytes) -> str:
    return hashlib.sha256(b"channel-frame/v3\0" + frame).hexdigest()
