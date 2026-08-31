"""Atomic trusted-boundary admission of untrusted CandidateProposal values."""

from __future__ import annotations

import base64
import binascii
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Tuple

from pydantic import ValidationError

from ..canonical import canonical_bytes, canonical_sha256
from ..models import CandidateProposal, MeasuredConfigurationId

MAX_PROPOSALS_PER_TURN = 16
MAX_PROPOSALS_PER_PROBLEM = 32
MAX_PROPOSALS_PER_CAMPAIGN = 128
MAX_CANONICAL_PAYLOAD_BYTES = 64 * 1024
MAX_STATEMENT_UTF8_BYTES = 2048
MAX_SOURCE_REFS = 16
MAX_QUEUE_DEPTH = 32
BURST_ATTEMPTS = 16
BURST_SECONDS = 1.0
ROLLING_ATTEMPTS = 32
ROLLING_SECONDS = 60.0
FORBIDDEN_TRUST_KEYS = frozenset(
    {
        "gate",
        "decision",
        "closure",
        "origin",
        "validator",
        "validated",
        "tenant_id",
        "audit_head",
        "rxp_root",
    }
)


class CandidateRejected(ValueError):
    pass


@dataclass(frozen=True)
class CandidateContext:
    campaign_id: str
    configuration_id: str
    tenant: str
    problem_id: str
    task_id: str
    generation: int
    turn: str
    attempt: str
    idempotency_key: str

    def __post_init__(self) -> None:
        MeasuredConfigurationId(self.configuration_id)
        if (
            not all(
                isinstance(value, str) and value
                for value in (
                    self.campaign_id,
                    self.tenant,
                    self.problem_id,
                    self.task_id,
                    self.turn,
                    self.attempt,
                    self.idempotency_key,
                )
            )
            or self.generation < 1
        ):
            raise ValueError("candidate trusted context is incomplete")

    @property
    def boundary(self) -> Tuple[str, str, str, str, str, str, int]:
        return (
            self.campaign_id,
            self.configuration_id,
            self.tenant,
            self.problem_id,
            self.task_id,
            self.turn,
            self.generation,
        )

    @property
    def attempt_key(self) -> Tuple[str, str, str, str, str, str, int, str, str]:
        return self.boundary + (self.attempt, self.idempotency_key)


@dataclass
class CandidateQuotaLedger:
    monotonic: Optional[Callable[[], float]] = None
    _turns: Counter = field(default_factory=Counter)
    _problems: Counter = field(default_factory=Counter)
    _campaigns: Counter = field(default_factory=Counter)
    _queues: Dict[Tuple[str, str, str], Dict[str, Tuple[object, ...]]] = field(default_factory=dict)
    _rates: Dict[Tuple[str, str, str], Deque[float]] = field(default_factory=dict)
    schema_rejections: List[Dict[str, str]] = field(default_factory=list)
    scanner_rejections: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.monotonic is None:
            self.monotonic = time.monotonic

    def reserve(self, context: CandidateContext) -> None:
        turn_key = context.boundary
        problem_key = context.boundary[:5]
        campaign_key = context.boundary[:3]
        if self._turns[turn_key] >= MAX_PROPOSALS_PER_TURN:
            raise CandidateRejected("turn_quota_exhausted")
        if self._problems[problem_key] >= MAX_PROPOSALS_PER_PROBLEM:
            raise CandidateRejected("problem_quota_exhausted")
        if self._campaigns[campaign_key] >= MAX_PROPOSALS_PER_CAMPAIGN:
            raise CandidateRejected("campaign_quota_exhausted")
        self._turns[turn_key] += 1
        self._problems[problem_key] += 1
        self._campaigns[campaign_key] += 1
        rate = self._rates.setdefault(campaign_key, deque())
        now = self.monotonic()  # type: ignore[misc]
        while rate and now - rate[0] > ROLLING_SECONDS:
            rate.popleft()
        burst = sum(1 for value in rate if now - value <= BURST_SECONDS)
        rate.append(now)
        if burst >= BURST_ATTEMPTS:
            raise CandidateRejected("burst_rate_exhausted")
        if len(rate) > ROLLING_ATTEMPTS:
            raise CandidateRejected("rolling_rate_exhausted")

    def enqueue(self, context: CandidateContext, receipt_id: str) -> None:
        campaign_key = context.boundary[:3]
        queue = self._queues.setdefault(campaign_key, {})
        if len(queue) >= MAX_QUEUE_DEPTH:
            raise CandidateRejected("queue_depth_exhausted")
        queue[receipt_id] = context.attempt_key

    def complete(self, context: CandidateContext, receipt_id: str) -> None:
        queue = self._queues.setdefault(context.boundary[:3], {})
        if queue.get(receipt_id) != context.attempt_key:
            raise CandidateRejected("queue_completion_mismatch")
        del queue[receipt_id]

    def queue(self, context: CandidateContext) -> int:
        return len(self._queues.get(context.boundary[:3], {}))

    def record_schema_rejection(self, digest: str, reason: str) -> None:
        self.schema_rejections.append({"proposal_digest": digest, "reason": reason})

    def record_scanner_rejection(self, source_class: str, reason: str) -> None:
        self.scanner_rejections.append({"source_class": source_class, "reason": reason, "count": 1})


class CandidateRpc:
    def __init__(
        self, *, ledger: CandidateQuotaLedger, monotonic: Optional[Callable[[], float]] = None
    ) -> None:
        self.ledger = ledger
        self._clock = monotonic
        self._lock = threading.RLock()
        self._receipts: Dict[Tuple[object, ...], Tuple[str, bytes, str]] = {}
        self._proposal_boundaries: Dict[str, Tuple[object, ...]] = {}

    def propose(self, proposal: Mapping[str, Any], *, context: CandidateContext) -> bytes:
        if not isinstance(proposal, Mapping):
            with self._lock:
                self.ledger.reserve(context)
            raise CandidateRejected("schema_invalid")
        document = dict(proposal)
        proposal_id = document.get("proposal_id")
        with self._lock:
            if document.get("task_id") != context.task_id:
                raise CandidateRejected("task_mismatch")
            if document.get("generation") != context.generation:
                raise CandidateRejected("generation_mismatch")
            prior = self._receipts.get(context.attempt_key)
            if prior is not None:
                try:
                    digest = canonical_sha256("candidate-proposal", document)
                except (TypeError, ValueError, UnicodeError):
                    raise CandidateRejected("schema_invalid")
                if prior[0] == digest:
                    return prior[1]
                raise CandidateRejected("idempotency_reuse_with_different_bytes")
            if (
                isinstance(proposal_id, str)
                and proposal_id in self._proposal_boundaries
                and self._proposal_boundaries[proposal_id] != context.boundary
            ):
                raise CandidateRejected("cross_boundary_duplicate")
            self.ledger.reserve(context)
            if _has_forbidden_key(document):
                self.ledger.record_scanner_rejection("proposal", "forbidden_field")
                raise CandidateRejected("forbidden_field")
            try:
                digest = canonical_sha256("candidate-proposal", document)
                self._limits(document)
                candidate = CandidateProposal.model_validate(document)
            except CandidateRejected as exc:
                self.ledger.record_schema_rejection(
                    canonical_sha256("candidate-proposal", document), str(exc)
                )
                raise
            except (TypeError, ValueError, ValidationError, UnicodeError) as exc:
                try:
                    self.ledger.record_schema_rejection(
                        canonical_sha256("candidate-proposal", document), "schema_invalid"
                    )
                except (TypeError, ValueError, UnicodeError):
                    pass
                raise CandidateRejected("schema_invalid") from exc
            receipt_id = "candidate-" + context.attempt
            self.ledger.enqueue(context, receipt_id)
            receipt = canonical_bytes(
                {
                    "schema_version": "secure-memory-candidate-receipt/v1",
                    "proposal_id": candidate.proposal_id,
                    "proposal_digest": canonical_sha256("candidate-proposal", candidate),
                    "receipt_id": receipt_id,
                }
            )
            self._receipts[context.attempt_key] = (digest, receipt, receipt_id)
            self._proposal_boundaries[candidate.proposal_id] = context.boundary
            return receipt

    def complete(self, *, context: CandidateContext, receipt_id: str) -> None:
        with self._lock:
            self.ledger.complete(context, receipt_id)

    @staticmethod
    def _limits(document: Mapping[str, Any]) -> None:
        if len(canonical_bytes(document)) > MAX_CANONICAL_PAYLOAD_BYTES:
            raise CandidateRejected("payload_too_large")
        refs = document.get("source_refs")
        if isinstance(refs, (list, tuple)) and len(refs) > MAX_SOURCE_REFS:
            raise CandidateRejected("too_many_source_refs")
        statement = document.get("statement_utf8_base64")
        if not isinstance(statement, str):
            raise CandidateRejected("schema_invalid")
        try:
            decoded = base64.b64decode(statement, validate=True)
            decoded.decode("utf-8", errors="strict")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise CandidateRejected("schema_invalid") from exc
        if len(decoded) > MAX_STATEMENT_UTF8_BYTES:
            raise CandidateRejected("statement_too_long")


def _has_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            (isinstance(key, str) and key.casefold() in FORBIDDEN_TRUST_KEYS)
            or _has_forbidden_key(child)
            for key, child in value.items()
        )
    return isinstance(value, (list, tuple)) and any(_has_forbidden_key(child) for child in value)
