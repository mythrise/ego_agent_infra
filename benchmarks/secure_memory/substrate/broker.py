"""Mockable fail-closed provider broker; it never opens a secret path itself."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Protocol

from ..canonical import canonical_bytes
from ..models import ModelRequest, SignedTaskLease
from .budget import BudgetDenied, BudgetLedger, RawUsage, SettledUsage
from .clock import Clock, SystemMonotonicClock


class BrokerDenied(ValueError):
    pass


class BrokerState(str, Enum):
    LOCKED = "LOCKED"
    QUALIFIED = "QUALIFIED"
    FROZEN = "FROZEN"


class ProviderRequestShape(str, Enum):
    CHAT_COMPLETIONS = "chat_completions/v1"


@dataclass(frozen=True)
class ProviderCapabilityRecord:
    state: BrokerState
    endpoint: str
    method: str
    body_shape: ProviderRequestShape
    model: str
    hard_output_limit: bool
    role_attribution: bool
    authoritative_usage: bool
    streaming_semantics: bool
    zero_background_calls: bool

    def usable(self) -> bool:
        return self.state is BrokerState.QUALIFIED and all((
            self.endpoint == "/chat/completions", self.method == "POST",
            self.body_shape is ProviderRequestShape.CHAT_COMPLETIONS, self.model == "agnes-2.5-pro",
            self.hard_output_limit, self.role_attribution, self.authoritative_usage,
            self.streaming_semantics, self.zero_background_calls,
        ))


@dataclass(frozen=True)
class ProviderReply:
    raw_usage: Optional[Mapping[str, int]]
    output_text: str
    first_stream_ns: Optional[int] = None
    first_content_ns: Optional[int] = None


class ProviderTransport(Protocol):
    def send(self, *, method: str, endpoint: str, body: Mapping[str, Any], api_key: Optional[bytes],
             allow_redirects: bool, tls_verified: bool) -> ProviderReply:
        """Perform the sole permitted provider request."""


@dataclass(frozen=True)
class BrokerResponse:
    output_text: str
    usage: SettledUsage
    first_stream_ns: Optional[int]
    first_content_ns: Optional[int]


def read_authorized_secret_fd(fd: int, *, expected_uid: int) -> bytes:
    """Read an already-open regular owner-only descriptor after re-validating it."""
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != expected_uid or (info.st_mode & 0o077):
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
    def __init__(self, *, ledger: BudgetLedger, capability: ProviderCapabilityRecord,
                 transport: ProviderTransport, signature_verifier: Callable[[object], bool],
                 secret_fd: Optional[int], clock: Optional[Clock] = None,
                 tokenizer: Optional[Callable[[bytes], int]] = None,
                 scanner: Optional[Callable[[bytes], bool]] = None) -> None:
        self._ledger = ledger
        self._capability = capability
        self._transport = transport
        self._signature_verifier = signature_verifier
        self._api_key = None if secret_fd is None else read_authorized_secret_fd(secret_fd, expected_uid=os.getuid())
        self._clock = clock or SystemMonotonicClock()
        self._tokenizer = tokenizer or (lambda body: len(body) // 4)
        self._scanner = scanner or (lambda body: True)

    def dispatch(self, request: ModelRequest, *, lease: SignedTaskLease, requester_role: str) -> BrokerResponse:
        if self._capability.state is BrokerState.LOCKED:
            raise BrokerDenied("capability_locked")
        if not self._capability.usable():
            self._capability = ProviderCapabilityRecord(**{**self._capability.__dict__, "state": BrokerState.FROZEN})
            raise BrokerDenied("capability")
        ticket = self._ledger.trusted_ticket(request.ticket_id)
        if ticket is None or not self._signature_verifier(lease) or not self._signature_verifier(ticket):
            raise BrokerDenied("signature")
        if request.provider_model != self._capability.model or request.max_output_tokens != ticket.max_output_tokens:
            raise BrokerDenied("qualified_request")
        if request.request_class != ticket.effective_request_class or request.campaign_id != ticket.campaign_id:
            raise BrokerDenied("trusted_binding")
        body = {
            "model": request.provider_model, "messages": list(request.messages), "max_tokens": request.max_output_tokens,
            "temperature": request.temperature, "top_p": request.top_p, "stream": request.stream, "tools": list(request.tools),
        }
        visible = canonical_bytes(body)
        if not self._scanner(visible):
            raise BrokerDenied("model_bytes_rejected")
        try:
            self._ledger.reserve(request.ticket_id, lease, requester_role=requester_role,
                                 tokenizer_estimate=self._tokenizer(visible), calibrated_positive_error=0,
                                 serialized_model_visible_bytes=visible)
            self._ledger.mark_dispatched(request.ticket_id)
        except BudgetDenied as exc:
            raise BrokerDenied(str(exc)) from exc
        try:
            reply = self._transport.send(method=self._capability.method, endpoint=self._capability.endpoint, body=body,
                                         api_key=self._api_key, allow_redirects=False, tls_verified=True)
        except Exception as exc:
            self._ledger.retain(request.ticket_id, "provider_failure")
            raise BrokerDenied("provider_failure") from exc
        if reply.raw_usage is None:
            self._ledger.retain(request.ticket_id, "unknown_usage")
            raise BrokerDenied("authoritative_usage_missing")
        try:
            usage = self._ledger.settle(request.ticket_id, RawUsage(**reply.raw_usage))
        except (BudgetDenied, ValueError) as exc:
            try:
                self._ledger.retain(request.ticket_id, "contradictory_usage")
            except BudgetDenied:
                pass
            raise BrokerDenied("authoritative_usage_invalid") from exc
        now = self._clock.monotonic_ns()
        return BrokerResponse(reply.output_text, usage,
                              reply.first_stream_ns if reply.first_stream_ns is not None else now,
                              reply.first_content_ns if reply.first_content_ns is not None else now)
