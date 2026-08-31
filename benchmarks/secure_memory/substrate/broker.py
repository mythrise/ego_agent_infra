"""Mockable fail-closed provider broker; it never opens a secret path itself."""

from __future__ import annotations

import os
import stat
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Protocol

from ..canonical import canonical_bytes, canonical_sha256
from ..models import ModelRequest, SignedTaskLease
from .budget import BudgetDenied, BudgetLedger, RawUsage, SettledUsage
from .clock import Clock, SystemMonotonicClock


class BrokerDenied(ValueError):
    pass


class ProviderTransportFailure(Exception):
    """Typed, message-free transport classification used for retry policy."""
    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)

    @classmethod
    def timeout(cls) -> "ProviderTransportFailure":
        return cls("timeout")

    @classmethod
    def status(cls, code: int) -> "ProviderTransportFailure":
        if code == 429 or 500 <= code <= 599 or 400 <= code <= 499:
            return cls("429" if code == 429 else ("5xx" if code >= 500 else "4xx"))
        raise ValueError("unsupported provider status")


class BrokerState(str, Enum):
    LOCKED = "LOCKED"
    QUALIFIED = "QUALIFIED"
    FROZEN = "FROZEN"


class ProviderRequestShape(str, Enum):
    CHAT_COMPLETIONS = "chat_completions/v1"


QUALIFICATION_CASES = (
    "basic_nonstream_body", "stream_first_content", "tool_call_id", "tool_result_continuation",
    "hard_output_boundary", "context_overlimit_refusal", "authoritative_total_usage",
    "cached_input_subset", "reasoning_output_subset", "429_retry", "5xx_retry", "timeout",
    "redirect", "tls", "multi_role_attribution", "idle_window_zero_background_call",
)


@dataclass(frozen=True)
class ProviderCapabilityRecord:
    state: BrokerState
    campaign_id: str
    project_id: str
    base_url: str
    endpoint: str
    method: str
    body_shape: ProviderRequestShape
    model: str
    hard_output_limit: bool
    role_attribution: bool
    authoritative_usage: bool
    streaming_semantics: bool
    zero_background_calls: bool
    calibrated_positive_error: int = 0
    temperature_present: bool = True
    top_p_present: bool = True
    matrix_cases: tuple[str, ...] = ()
    matrix_digest: str = ""
    issuer_id: str = ""
    key_id: str = ""
    issue_sequence: int = 0
    expires_at_sequence: int = 0
    record_sha256: str = ""
    signature_base64: str = ""

    def usable(self) -> bool:
        return self.state is BrokerState.QUALIFIED and all((
            self.endpoint == "/chat/completions", self.method == "POST",
            self.body_shape is ProviderRequestShape.CHAT_COMPLETIONS, self.model == "agnes-2.5-pro",
            self.hard_output_limit, self.role_attribution, self.authoritative_usage,
            self.streaming_semantics, self.zero_background_calls,
        ))


class CampaignCapabilityAuthority:
    """One lock-protected capability state shared by every campaign broker."""

    def __init__(self, record: ProviderCapabilityRecord, *, signature_verifier: Callable[[object], bool],
                 current_sequence: Callable[[], int], expected_campaign_id: str,
                 expected_project_id: str, expected_issuer_id: str, expected_key_id: str) -> None:
        sequence = current_sequence()
        core = {key: value for key, value in record.__dict__.items() if key not in {"record_sha256", "signature_base64"}}
        expected_matrix = canonical_sha256("agentteams-capability-matrix", record.matrix_cases)
        if (
            not signature_verifier(record) or record.record_sha256 != canonical_sha256("provider-capability-record", core)
            or record.matrix_cases != QUALIFICATION_CASES
            or record.matrix_digest != expected_matrix or record.campaign_id != expected_campaign_id
            or record.project_id != expected_project_id or record.issuer_id != expected_issuer_id
            or record.key_id != expected_key_id or record.state is not BrokerState.QUALIFIED
            or record.calibrated_positive_error < 0 or record.issue_sequence > sequence or sequence > record.expires_at_sequence
        ):
            raise BrokerDenied("capability_signature")
        self._record = record
        self._current_sequence = current_sequence
        self._lock = threading.RLock()

    def record(self) -> ProviderCapabilityRecord:
        with self._lock:
            try:
                sequence = self._current_sequence()
            except Exception:
                self._freeze_unlocked()
                raise BrokerDenied("capability_sequence") from None
            if self._record.issue_sequence > sequence:
                self._freeze_unlocked()
                raise BrokerDenied("capability_not_yet_valid")
            if sequence > self._record.expires_at_sequence:
                self._freeze_unlocked()
                raise BrokerDenied("capability_expired")
            return self._record

    def freeze(self) -> None:
        with self._lock:
            self._freeze_unlocked()

    def _freeze_unlocked(self) -> None:
        self._record = ProviderCapabilityRecord(
            **{**self._record.__dict__, "state": BrokerState.FROZEN}
        )

    def observe_unattributed_call(self) -> None:
        self.freeze()


@dataclass(frozen=True)
class ProviderReply:
    raw_usage: Optional[Mapping[str, int]]
    output_text: str
    first_stream_ns: Optional[int] = None
    first_content_ns: Optional[int] = None


class ProviderTransport(Protocol):
    def send(self, *, base_url: str, method: str, endpoint: str, body: Mapping[str, Any], api_key: Optional[bytes],
             allow_redirects: bool, tls_verified: bool) -> ProviderReply:
        """Perform the sole permitted provider request."""


@dataclass(frozen=True)
class BrokerResponse:
    output_text: str
    usage: SettledUsage
    first_stream_ns: Optional[int]
    first_content_ns: Optional[int]


@dataclass(frozen=True)
class SecretDescriptorHandoff:
    """A broker may receive this descriptor, never a credential path."""

    fd: int
    device: int
    inode: int
    pass_fds: tuple[int, ...]
    close_fds: bool = True


def provision_secret_descriptor(
    path: str,
    *,
    expected_uid: int,
    authorize_mode_repair: bool = False,
    opener: Callable[[str, int], int] = os.open,
) -> SecretDescriptorHandoff:
    """Open one approved regular file exactly once and bind its inode for handoff.

    This intentionally has no default credential location.  Controller code may
    call it once, then passes only the returned descriptor to a confined broker.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = opener(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != expected_uid:
            raise BrokerDenied("secret_descriptor_invalid")
        if info.st_mode & 0o077:
            if not authorize_mode_repair:
                raise BrokerDenied("secret_descriptor_invalid")
            os.fchmod(fd, 0o600)
            info = os.fstat(fd)
        if info.st_mode & 0o077:
            raise BrokerDenied("secret_descriptor_invalid")
        return SecretDescriptorHandoff(fd=fd, device=info.st_dev, inode=info.st_ino, pass_fds=(fd,))
    except Exception:
        os.close(fd)
        raise


def read_authorized_secret_fd(
    fd: int, *, expected_uid: int, expected_device: Optional[int] = None,
    expected_inode: Optional[int] = None,
) -> bytes:
    """Read an already-open regular owner-only descriptor after re-validating it."""
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode) or info.st_uid != expected_uid or (info.st_mode & 0o077)
        or (expected_device is not None and info.st_dev != expected_device)
        or (expected_inode is not None and info.st_ino != expected_inode)
    ):
        raise BrokerDenied("secret_descriptor_invalid")
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        chunks.append(chunk)
    secret = b"".join(chunks)
    if not secret:
        raise BrokerDenied("secret_descriptor_empty")
    return secret


class ProviderBroker:
    def __init__(self, *, ledger: BudgetLedger, transport: ProviderTransport,
                 signature_verifier: Callable[[object], bool], secret_handoff: Optional[SecretDescriptorHandoff],
                 capability: Optional[ProviderCapabilityRecord] = None,
                 capability_authority: Optional[CampaignCapabilityAuthority] = None,
                 clock: Optional[Clock] = None,
                 tokenizer: Optional[Callable[[bytes], int]] = None,
                 scanner: Optional[Callable[[bytes], bool]] = None) -> None:
        self._ledger = ledger
        if capability_authority is None:
            if capability is None:
                raise BrokerDenied("capability_required")
            raise BrokerDenied("capability_authority_required")
        self._capability_authority = capability_authority
        self._transport = transport
        self._signature_verifier = signature_verifier
        if secret_handoff is not None and (not secret_handoff.close_fds or secret_handoff.pass_fds != (secret_handoff.fd,)):
            raise BrokerDenied("secret_handoff_invalid")
        self._api_key = None if secret_handoff is None else read_authorized_secret_fd(
            secret_handoff.fd, expected_uid=os.getuid(), expected_device=secret_handoff.device,
            expected_inode=secret_handoff.inode)
        self._clock = clock or SystemMonotonicClock()
        self._tokenizer = tokenizer or (lambda body: len(body) // 4)
        self._scanner = scanner or (lambda body: True)

    def dispatch(self, request: ModelRequest, *, lease: SignedTaskLease, requester_role: str,
                 retry_ticket_id: Optional[str] = None,
                 backoff_observer: Callable[[str], None] = lambda _kind: None) -> BrokerResponse:
        capability = self._capability_authority.record()
        if capability.state is BrokerState.FROZEN:
            raise BrokerDenied("capability_frozen")
        if capability.state is BrokerState.LOCKED:
            raise BrokerDenied("capability_locked")
        if not capability.usable():
            self._capability_authority.freeze()
            raise BrokerDenied("capability")
        if capability.calibrated_positive_error != self._ledger.calibrated_positive_error:
            self._capability_authority.freeze()
            raise BrokerDenied("capability_calibration")
        ticket = self._ledger.trusted_ticket(request.ticket_id)
        if ticket is None or not self._signature_verifier(lease) or not self._signature_verifier(ticket):
            raise BrokerDenied("signature")
        if request.lease_sha256 != lease.core_sha256:
            raise BrokerDenied("lease_digest")
        if (request.provider_model != capability.model or request.max_input_tokens != ticket.max_input_tokens
                or request.max_output_tokens != ticket.max_output_tokens):
            raise BrokerDenied("qualified_request")
        if request.request_class != ticket.effective_request_class or request.campaign_id != ticket.campaign_id:
            raise BrokerDenied("trusted_binding")
        if request.provider_base_url != capability.base_url:
            raise BrokerDenied("qualified_request")
        body = {
            "model": request.provider_model, "messages": list(request.messages), "max_tokens": request.max_output_tokens,
            "temperature": request.temperature, "top_p": request.top_p, "stream": request.stream, "tools": list(request.tools),
        }
        if not capability.temperature_present:
            body.pop("temperature")
        if not capability.top_p_present:
            body.pop("top_p")
        if capability.temperature_present != (request.temperature is not None):
            raise BrokerDenied("qualified_request")
        if capability.top_p_present != (request.top_p is not None):
            raise BrokerDenied("qualified_request")
        visible = canonical_bytes(body)
        if not self._scanner(visible):
            raise BrokerDenied("model_bytes_rejected")
        try:
            self._ledger.reserve(request.ticket_id, lease, requester_role=requester_role,
                                 tokenizer_estimate=self._tokenizer(visible),
                                 calibrated_positive_error=capability.calibrated_positive_error,
                                 serialized_model_visible_bytes=visible)
            self._ledger.mark_dispatched(request.ticket_id)
        except BudgetDenied as exc:
            raise BrokerDenied(str(exc)) from exc
        try:
            reply = self._transport.send(base_url=request.provider_base_url, method=capability.method,
                                         endpoint=capability.endpoint, body=body,
                                         api_key=self._api_key, allow_redirects=False, tls_verified=True)
        except ProviderTransportFailure as failure:
            self._ledger.retain(request.ticket_id, failure.kind)
            if retry_ticket_id is not None and failure.kind in {"429", "5xx", "timeout"}:
                backoff_observer(failure.kind)
                retry_capability = self._capability_authority.record()
                if retry_capability.state is BrokerState.FROZEN:
                    raise BrokerDenied("capability_frozen")
                if not retry_capability.usable():
                    self._capability_authority.freeze()
                    raise BrokerDenied("capability")
                if (
                    retry_capability.calibrated_positive_error
                    != self._ledger.calibrated_positive_error
                ):
                    self._capability_authority.freeze()
                    raise BrokerDenied("capability_calibration")
                self._ledger.reserve_retry(retry_ticket_id, request.ticket_id, lease,
                                           requester_role=requester_role, tokenizer_estimate=self._tokenizer(visible),
                                           calibrated_positive_error=retry_capability.calibrated_positive_error,
                                           serialized_model_visible_bytes=visible)
                self._ledger.mark_dispatched(retry_ticket_id)
                try:
                    reply = self._transport.send(base_url=request.provider_base_url,
                                                 method=retry_capability.method,
                                                 endpoint=retry_capability.endpoint,
                                                 body=body, api_key=self._api_key,
                                                 allow_redirects=False, tls_verified=True)
                except Exception:
                    self._ledger.retain(retry_ticket_id, "provider_failure")
                    raise BrokerDenied("provider_failure") from None
                if reply.raw_usage is None:
                    self._ledger.retain(retry_ticket_id, "unknown_usage")
                    self._capability_authority.freeze()
                    raise BrokerDenied("authoritative_usage_missing")
                try:
                    usage = self._ledger.settle(retry_ticket_id, RawUsage(**reply.raw_usage))
                except (BudgetDenied, ValueError):
                    self._ledger.retain(retry_ticket_id, "contradictory_usage")
                    self._capability_authority.freeze()
                    raise BrokerDenied("authoritative_usage_invalid") from None
                return BrokerResponse(reply.output_text, usage, reply.first_stream_ns, reply.first_content_ns)
            raise BrokerDenied("provider_failure") from None
        except Exception:
            self._ledger.retain(request.ticket_id, "provider_failure")
            raise BrokerDenied("provider_failure") from None
        if reply.raw_usage is None:
            self._ledger.retain(request.ticket_id, "unknown_usage")
            self._capability_authority.freeze()
            raise BrokerDenied("authoritative_usage_missing")
        try:
            usage = self._ledger.settle(request.ticket_id, RawUsage(**reply.raw_usage))
        except (BudgetDenied, ValueError):
            try:
                self._ledger.retain(request.ticket_id, "contradictory_usage")
            except BudgetDenied:
                pass
            self._capability_authority.freeze()
            raise BrokerDenied("authoritative_usage_invalid") from None
        return BrokerResponse(reply.output_text, usage,
                              reply.first_stream_ns, reply.first_content_ns)
