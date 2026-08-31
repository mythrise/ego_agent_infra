"""Append-only, fail-closed reservations for provider calls.

Cache and reasoning counters are provider-reported subtotals.  They are checked
for consistency but never added to billed input/output totals.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Iterable, Optional, Tuple

from pydantic import Field, model_validator

from ..canonical import canonical_sha256
from ..models import IssuedBudgetTicket, RequestClass, SignedTaskLease, StrictModel, TicketTemplate


class BudgetDenied(ValueError):
    """A trusted budget invariant prevented a provider dispatch."""


@dataclass(frozen=True)
class BudgetTrustContext:
    issuer_id: str
    key_id: str
    current_sequence: Callable[[], int]
    signature_verifier: Callable[[object], bool]


@dataclass(frozen=True)
class BudgetTriple:
    requests: int
    input: int
    output: int

    def add(self, *, requests: int = 0, input: int = 0, output: int = 0) -> "BudgetTriple":
        return BudgetTriple(self.requests + requests, self.input + input, self.output + output)


@dataclass(frozen=True)
class RequestLimit:
    max_input: int
    max_output: int


REQUEST_LIMITS = {
    RequestClass.MAIN: RequestLimit(max_input=10_000, max_output=1_500),
    RequestClass.AUXILIARY: RequestLimit(max_input=6_000, max_output=750),
    RequestClass.REVIEW: RequestLimit(max_input=8_000, max_output=1_000),
}
CAMPAIGN_RESERVATION = BudgetTriple(requests=356, input=3_306_000, output=485_500)
CAMPAIGN_ABSOLUTE = BudgetTriple(requests=360, input=4_000_000, output=600_000)


class ReservationState(str, Enum):
    RESERVED = "RESERVED"
    DISPATCHED = "DISPATCHED"
    SETTLED = "SETTLED"
    RETAINED = "RETAINED"


class RawUsage(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_tokens: Optional[int] = Field(default=None, ge=0)
    cache_write_tokens: Optional[int] = Field(default=None, ge=0)
    reasoning_tokens: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_subsets(self) -> "RawUsage":
        if self.reasoning_tokens is not None and self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning_tokens must be an output subtotal")
        for value in (self.cache_read_tokens, self.cache_write_tokens):
            if value is not None and value > self.input_tokens:
                raise ValueError("cache token counts must be input subtotals")
        return self


class SettledUsage(StrictModel):
    raw_usage: RawUsage
    budget_input: int = Field(ge=0)
    budget_output: int = Field(ge=0)
    comparable_input: int = Field(ge=0)
    comparable_output: int = Field(ge=0)


@dataclass(frozen=True)
class Reservation:
    ticket_id: str
    template_id: str
    reserved_input: int
    reserved_output: int
    state: ReservationState
    sequence: int
    settled_usage: Optional[SettledUsage] = None
    retained_reason: Optional[str] = None


@dataclass(frozen=True)
class BudgetEvent:
    sequence: int
    ticket_id: str
    state: ReservationState
    reserved_input: int
    reserved_output: int


class BudgetLedger:
    """In-memory trusted projection of append-only reservation events.

    Its lock makes check, event append, and projection update one transaction.
    Callers may observe events but cannot mutate tickets/templates after load.
    """

    def __init__(
        self,
        *,
        templates: Iterable[TicketTemplate],
        tickets: Iterable[IssuedBudgetTicket],
        manifest_sha256: str,
        trust_context: BudgetTrustContext,
    ) -> None:
        template_items = tuple(templates)
        ticket_items = tuple(tickets)
        self._templates: Dict[str, TicketTemplate] = {item.template_id: item for item in template_items}
        self._tickets: Dict[str, IssuedBudgetTicket] = {item.ticket_id: item for item in ticket_items}
        if not template_items:
            raise BudgetDenied("templates_required")
        if len(self._templates) != len(template_items):
            raise BudgetDenied("duplicate_template")
        if len(self._tickets) != len(ticket_items):
            raise BudgetDenied("duplicate_ticket")
        if len({item.issue_sequence for item in ticket_items}) != len(ticket_items):
            raise BudgetDenied("duplicate_issue_sequence")
        if len({item.template_id for item in ticket_items}) != len(ticket_items):
            raise BudgetDenied("template_ticket")
        if any(ticket.template_id not in self._templates for ticket in self._tickets.values()):
            raise BudgetDenied("unknown_template")
        if any(ticket.manifest_sha256 != manifest_sha256 for ticket in self._tickets.values()):
            raise BudgetDenied("manifest")
        self._manifest_sha256 = manifest_sha256
        self._trust_context = trust_context
        if not trust_context.issuer_id or not trust_context.key_id:
            raise BudgetDenied("trust_identity")
        current = trust_context.current_sequence()
        self._reservation_cap = CAMPAIGN_RESERVATION
        self._absolute_cap = CAMPAIGN_ABSOLUTE
        self._reservations: Dict[str, Reservation] = {}
        self._used_templates: set[str] = set()
        self._events: Tuple[BudgetEvent, ...] = ()
        self._lock = threading.RLock()
        for ticket in ticket_items:
            template = self._templates[ticket.template_id]
            ticket_core = {
                key: value for key, value in ticket.model_dump(mode="python").items()
                if key not in {"ticket_sha256", "signature_base64"}
            }
            if (
                ticket.ticket_sha256 != canonical_sha256("issued-budget-ticket", ticket_core)
                or not trust_context.signature_verifier(ticket)
                or ticket.issuer_id != trust_context.issuer_id or ticket.key_id != trust_context.key_id
                or ticket.issue_sequence > current or current > ticket.expires_at_sequence
            ):
                raise BudgetDenied("ticket_sequence")
            retry = template.retry_owner is not None
            if retry and (
                template.retry_owner != template.execution_phase_owner
                or template.execution_phase_owner.value in {"QUALIFICATION", "OPTIMIZER"}
                or template.request_class is not RequestClass.MAIN
            ):
                raise BudgetDenied("retry_template")
            if (
                ticket.execution_phase_owner != template.execution_phase_owner
                or ticket.configuration_id != template.configuration_id
                or ticket.allowed_role != template.allowed_role
                or (not retry and ticket.effective_request_class != template.request_class)
                or (not retry and ticket.usage_phase != template.usage_phase)
                or (not retry and (ticket.max_input_tokens != template.max_input_tokens
                                  or ticket.max_output_tokens != template.max_output_tokens))
                or (retry and (ticket.max_input_tokens > REQUEST_LIMITS[ticket.effective_request_class].max_input
                               or ticket.max_output_tokens > REQUEST_LIMITS[ticket.effective_request_class].max_output))
            ):
                raise BudgetDenied("template_binding")

    @staticmethod
    def absolute_allows(total: BudgetTriple) -> bool:
        return (
            total.requests <= CAMPAIGN_ABSOLUTE.requests
            and total.input <= CAMPAIGN_ABSOLUTE.input
            and total.output <= CAMPAIGN_ABSOLUTE.output
        )

    @property
    def events(self) -> Tuple[BudgetEvent, ...]:
        with self._lock:
            return self._events

    @property
    def totals(self) -> BudgetTriple:
        with self._lock:
            return BudgetTriple(
                requests=len(self._reservations),
                input=sum(item.reserved_input for item in self._reservations.values()),
                output=sum(item.reserved_output for item in self._reservations.values()),
            )

    def trusted_ticket(self, ticket_id: str) -> Optional[IssuedBudgetTicket]:
        return self._tickets.get(ticket_id)

    def reservation_for(self, ticket_id: str) -> Optional[Reservation]:
        with self._lock:
            return self._reservations.get(ticket_id)

    def _append(self, reservation: Reservation) -> Reservation:
        event = BudgetEvent(
            sequence=len(self._events) + 1,
            ticket_id=reservation.ticket_id,
            state=reservation.state,
            reserved_input=reservation.reserved_input,
            reserved_output=reservation.reserved_output,
        )
        updated = Reservation(**{**reservation.__dict__, "sequence": event.sequence})
        self._events = self._events + (event,)
        self._reservations[updated.ticket_id] = updated
        return updated

    def reserve(
        self,
        ticket_id: str,
        lease: SignedTaskLease,
        *,
        requester_role: str,
        tokenizer_estimate: int,
        calibrated_positive_error: int,
        serialized_model_visible_bytes: bytes,
    ) -> Reservation:
        if min(tokenizer_estimate, calibrated_positive_error) < 0:
            raise BudgetDenied("negative_estimate")
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise BudgetDenied("untrusted_ticket")
            template = self._templates[ticket.template_id]
            current = self._trust_context.current_sequence()
            if ticket.issue_sequence > current or current > ticket.expires_at_sequence:
                raise BudgetDenied("ticket_sequence")
            if ticket.template_id in self._used_templates or ticket_id in self._reservations:
                raise BudgetDenied("ticket_consumed")
            core = lease.core
            if (
                not self._trust_context.signature_verifier(lease)
                or core.issuer_id != self._trust_context.issuer_id or core.key_id != self._trust_context.key_id
                or core.issue_sequence > current or current > core.expires_at_sequence
            ):
                raise BudgetDenied("lease_sequence")
            if ticket_id not in core.issued_ticket_ids:
                raise BudgetDenied("lease_ticket")
            if core.manifest_sha256 != self._manifest_sha256 or ticket.manifest_sha256 != self._manifest_sha256:
                raise BudgetDenied("manifest")
            if requester_role != ticket.allowed_role or core.role != ticket.allowed_role:
                raise BudgetDenied("role")
            bindings = (
                (core.campaign_id, ticket.campaign_id), (core.configuration_id, ticket.configuration_id),
                (core.execution_phase_owner, ticket.execution_phase_owner), (core.project_id, ticket.project_id),
                (core.task_id, ticket.task_id), (core.worker, ticket.worker),
                (core.matrix_user_id, ticket.matrix_user_id), (core.request_class, ticket.effective_request_class),
            )
            if any(left != right for left, right in bindings):
                raise BudgetDenied("lease_binding")
            if template.allowed_role != ticket.allowed_role or template.request_class != ticket.effective_request_class:
                raise BudgetDenied("template_binding")
            if ((template.problem_id is not None and template.problem_id != core.problem_id)
                    or (template.turn is not None and template.turn != core.turn)):
                raise BudgetDenied("template_row")
            estimated = tokenizer_estimate + calibrated_positive_error + 512
            byte_bound = len(serialized_model_visible_bytes) + 1_024
            reserved_input = max(estimated, byte_bound)
            limit = REQUEST_LIMITS[ticket.effective_request_class]
            if reserved_input > limit.max_input or reserved_input > ticket.max_input_tokens:
                raise BudgetDenied("input_ceiling")
            reserved_output = ticket.max_output_tokens
            if reserved_output > limit.max_output:
                raise BudgetDenied("output_ceiling")
            next_total = self.totals.add(requests=1, input=reserved_input, output=reserved_output)
            if (
                next_total.requests > self._reservation_cap.requests
                or next_total.input > self._reservation_cap.input
                or next_total.output > self._reservation_cap.output
                or next_total.requests > self._absolute_cap.requests
                or next_total.input > self._absolute_cap.input
                or next_total.output > self._absolute_cap.output
            ):
                raise BudgetDenied("campaign_reservation")
            self._used_templates.add(ticket.template_id)
            return self._append(Reservation(ticket_id, ticket.template_id, reserved_input, reserved_output,
                                            ReservationState.RESERVED, 0))

    def mark_dispatched(self, ticket_id: str) -> Reservation:
        with self._lock:
            value = self._require(ticket_id, ReservationState.RESERVED)
            return self._append(Reservation(**{**value.__dict__, "state": ReservationState.DISPATCHED}))

    def reserve_retry(self, retry_ticket_id: str, original_ticket_id: str, lease: SignedTaskLease, *,
                      requester_role: str, tokenizer_estimate: int, calibrated_positive_error: int,
                      serialized_model_visible_bytes: bytes) -> Reservation:
        """Consume one preloaded, owner-bound retry ticket after a retained transient original."""
        with self._lock:
            original = self._reservations.get(original_ticket_id)
            retry_ticket = self._tickets.get(retry_ticket_id)
            if original is None or original.state is not ReservationState.RETAINED:
                raise BudgetDenied("retry_original")
            if retry_ticket is None or retry_ticket_id not in lease.core.issued_ticket_ids:
                raise BudgetDenied("retry_ticket")
            template = self._templates[retry_ticket.template_id]
            original_ticket = self._tickets[original_ticket_id]
            if (
                template.retry_owner != original_ticket.execution_phase_owner
                or retry_ticket.effective_request_class != original_ticket.effective_request_class
                or retry_ticket.usage_phase != original_ticket.usage_phase
                or retry_ticket.max_input_tokens != original_ticket.max_input_tokens
                or retry_ticket.max_output_tokens != original_ticket.max_output_tokens
            ):
                raise BudgetDenied("retry_binding")
            return self.reserve(retry_ticket_id, lease, requester_role=requester_role,
                                tokenizer_estimate=tokenizer_estimate,
                                calibrated_positive_error=calibrated_positive_error,
                                serialized_model_visible_bytes=serialized_model_visible_bytes)

    def retain(self, ticket_id: str, reason: str) -> Reservation:
        with self._lock:
            value = self._require(ticket_id, ReservationState.DISPATCHED)
            return self._append(Reservation(**{**value.__dict__, "state": ReservationState.RETAINED,
                                                "retained_reason": "provider_failure"}))

    def settle(self, ticket_id: str, raw_usage: RawUsage) -> SettledUsage:
        with self._lock:
            value = self._require(ticket_id, ReservationState.DISPATCHED)
            if raw_usage.input_tokens > value.reserved_input or raw_usage.output_tokens > value.reserved_output:
                raise BudgetDenied("contradictory_usage")
            settled = SettledUsage(raw_usage=raw_usage, budget_input=raw_usage.input_tokens,
                                  budget_output=raw_usage.output_tokens, comparable_input=raw_usage.input_tokens,
                                  comparable_output=raw_usage.output_tokens)
            self._append(Reservation(**{**value.__dict__, "state": ReservationState.SETTLED,
                                        "settled_usage": settled}))
            return settled

    def _require(self, ticket_id: str, *allowed: ReservationState) -> Reservation:
        value = self._reservations.get(ticket_id)
        if value is None:
            raise BudgetDenied("unreserved_ticket")
        if value.state not in allowed:
            raise BudgetDenied("already_terminal")
        return value
