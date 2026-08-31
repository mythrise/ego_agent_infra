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
        self._heads: Dict[WindowKey, Tuple[int, str, InstalledReceipt]] = {}
        self._claims: Dict[Tuple[WindowKey, int], str] = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)

    @staticmethod
    def valid_record(record: InstalledReceipt, identity: Tuple[object, ...], digest: str) -> bool:
        if (
            not isinstance(record, InstalledReceipt)
            or record.identity != identity
            or record.request_frame_sha256 != digest
        ):
            return False
        expected = hashlib.sha256(
            b"receipt/v1\0" + canonical_bytes(identity) + digest.encode()
        ).hexdigest()
        if record.durable_receipt_id != expected or not record.receipt_frame:
            return False
        try:
            value = parse_json_bytes(record.receipt_frame)
        except (TypeError, ValueError):
            return False
        return (
            isinstance(value, dict)
            and bool(value)
            and canonical_bytes(value) == record.receipt_frame
        )

    def claim(
        self, window: WindowKey, sequence: int, digest: str
    ) -> Tuple[str, Optional[InstalledReceipt]]:
        with self._condition:
            reservation = (window, sequence)
            while reservation in self._claims:
                if self._claims[reservation] != digest:
                    return "conflict", None
                self._condition.wait()
            head = self._heads.get(window)
            if head is not None:
                if sequence <= head[0]:
                    historical = self._records.get(window + (sequence,))
                    if historical is None or not self.valid_record(
                        historical, window + (sequence,), digest
                    ):
                        return "inconsistent", None
                    return (
                        ("replay", historical)
                        if historical.request_frame_sha256 == digest
                        else ("conflict", None)
                    )
                if sequence != head[0] + 1:
                    return "mismatch", None
            elif sequence != 1:
                return "mismatch", None
            self._claims[reservation] = digest
            return "owner", None

    def abort(self, window: WindowKey, sequence: int, digest: str) -> None:
        with self._condition:
            if self._claims.get((window, sequence)) == digest:
                del self._claims[(window, sequence)]
                self._condition.notify_all()

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
                if not self.valid_record(current, identity, digest):
                    raise ChannelRejected("receipt_store_mismatch")
                return current
            receipt_id = hashlib.sha256(
                b"receipt/v1\0" + canonical_bytes(identity) + digest.encode()
            ).hexdigest()
            result = InstalledReceipt(digest, receipt_id, frame, identity)
            if not self.valid_record(result, identity, digest):
                raise ChannelRejected("invalid_durable_receipt")
            self._records[identity] = result
            return result

    def commit(
        self, window: WindowKey, sequence: int, digest: str, installed: InstalledReceipt
    ) -> None:
        with self._condition:
            if self._claims.get((window, sequence)) != digest:
                raise ChannelRejected("sequence_commit_conflict")
            self._heads[window] = (sequence, digest, installed)
            del self._claims[(window, sequence)]
            self._condition.notify_all()

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
        identity = key + (env.sequence,)
        state, existing = self.store.claim(key, env.sequence, digest)
        if state == "replay":
            return existing.receipt_frame  # type: ignore[union-attr]
        if state == "conflict":
            raise ChannelRejected("sequence_reuse_with_different_bytes")
        if state == "inconsistent":
            raise ChannelRejected("historical_receipt_inconsistent")
        if state == "mismatch":
            raise ChannelRejected("sequence_mismatch")
        try:
            pending = route(env)
            installed = self.store.install_or_get(identity, digest, pending)
            if not self.store.valid_record(installed, identity, digest):
                raise ChannelRejected("invalid_durable_receipt")
        except Exception:
            self.store.abort(key, env.sequence, digest)
            raise
        self.store.commit(key, env.sequence, digest, installed)
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
