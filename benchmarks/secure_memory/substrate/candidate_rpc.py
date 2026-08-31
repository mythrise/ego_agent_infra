"""Shared atomic trusted-context candidate admission ledger."""

from __future__ import annotations
import base64
import binascii
import math
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
        if self.generation < 1 or not all(
            isinstance(x, str) and x
            for x in (
                self.campaign_id,
                self.tenant,
                self.problem_id,
                self.task_id,
                self.turn,
                self.attempt,
                self.idempotency_key,
            )
        ):
            raise ValueError("candidate context")

    @property
    def semantic_boundary(self) -> Tuple[object, ...]:
        return (self.campaign_id, self.configuration_id, self.tenant, self.problem_id, self.turn)

    @property
    def attempt_key(self) -> Tuple[object, ...]:
        return self.semantic_boundary + (
            self.task_id,
            self.generation,
            self.attempt,
            self.idempotency_key,
        )


@dataclass
class CandidateQuotaLedger:
    monotonic: Optional[Callable[[], float]] = None
    _turns: Counter = field(default_factory=Counter)
    _problems: Counter = field(default_factory=Counter)
    _campaigns: Counter = field(default_factory=Counter)
    _rates: Dict[Tuple[object, ...], Deque[float]] = field(default_factory=dict)
    _queues: Dict[Tuple[object, ...], Dict[str, Tuple[object, ...]]] = field(default_factory=dict)
    _completed: Dict[str, Tuple[object, ...]] = field(default_factory=dict)
    _attempts: Dict[Tuple[object, ...], Tuple[Optional[str], Optional[bytes], Optional[str]]] = (
        field(default_factory=dict)
    )
    _proposals: Dict[str, Tuple[Tuple[object, ...], str, bytes]] = field(default_factory=dict)
    _lock: Any = field(default_factory=threading.RLock)
    _last_clock: Optional[float] = None
    schema_rejections: List[Dict[str, str]] = field(default_factory=list)
    scanner_rejections: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.monotonic is None:
            self.monotonic = time.monotonic

    def _now(self) -> float:
        value = self.monotonic()  # type: ignore[misc]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or (self._last_clock is not None and value < self._last_clock)
        ):
            raise CandidateRejected("clock_invalid")
        self._last_clock = float(value)
        return float(value)

    def reserve(self, c: CandidateContext) -> None:
        turn = c.semantic_boundary
        problem = turn[:4]
        campaign = turn[:3]
        if self._turns[turn] >= 16:
            raise CandidateRejected("turn_quota_exhausted")
        if self._problems[problem] >= 32:
            raise CandidateRejected("problem_quota_exhausted")
        if self._campaigns[campaign] >= 128:
            raise CandidateRejected("campaign_quota_exhausted")
        self._turns[turn] += 1
        self._problems[problem] += 1
        self._campaigns[campaign] += 1
        now = self._now()
        rate = self._rates.setdefault(campaign, deque())
        while rate and now - rate[0] >= 60.0:
            rate.popleft()
        burst = sum(1 for t in rate if now - t < 1.0)
        rate.append(now)
        if burst >= 16:
            raise CandidateRejected("burst_rate_exhausted")
        if len(rate) > 32:
            raise CandidateRejected("rolling_rate_exhausted")

    def queue(self, c: CandidateContext) -> int:
        return len(self._queues.get(c.semantic_boundary[:3], {}))

    def complete(self, c: CandidateContext, receipt_id: str) -> None:
        queue = self._queues.setdefault(c.semantic_boundary[:3], {})
        if queue.get(receipt_id) == c.attempt_key:
            del queue[receipt_id]
            self._completed[receipt_id] = c.attempt_key
            return
        if self._completed.get(receipt_id) == c.attempt_key:
            return
        raise CandidateRejected("queue_completion_mismatch")


class CandidateRpc:
    def __init__(
        self, *, ledger: CandidateQuotaLedger, monotonic: Optional[Callable[[], float]] = None
    ) -> None:
        self.ledger = ledger

    def propose(self, proposal: Mapping[str, Any], *, context: CandidateContext) -> bytes:
        doc = dict(proposal) if isinstance(proposal, Mapping) else None
        try:
            digest = canonical_sha256("candidate-proposal", doc) if doc is not None else None
        except (TypeError, ValueError, UnicodeError):
            digest = None
        with self.ledger._lock:
            prior = self.ledger._attempts.get(context.attempt_key)
            if prior is not None:
                if prior[0] is not None and prior[0] != digest:
                    raise CandidateRejected("idempotency_reuse_with_different_bytes")
                if prior[1] is not None:
                    return prior[1]
                raise CandidateRejected(prior[2] or "schema_invalid")
            if (
                doc is not None
                and isinstance(doc.get("proposal_id"), str)
                and doc["proposal_id"] in self.ledger._proposals
            ):
                boundary, old_digest, old_receipt = self.ledger._proposals[doc["proposal_id"]]
                if boundary == context.semantic_boundary and old_digest == digest:
                    self.ledger._attempts[context.attempt_key] = (digest, old_receipt, None)
                    return old_receipt
                raise CandidateRejected("cross_boundary_duplicate")
            try:
                self.ledger.reserve(context)
                if doc is None:
                    raise CandidateRejected("schema_invalid")
                if doc.get("task_id") != context.task_id:
                    raise CandidateRejected("task_mismatch")
                if doc.get("generation") != context.generation:
                    raise CandidateRejected("generation_mismatch")
                if _forbidden(doc):
                    self.ledger.scanner_rejections.append(
                        {"source_class": "proposal", "reason": "forbidden_field", "count": 1}
                    )
                    raise CandidateRejected("forbidden_field")
                self._limits(doc)
                candidate = CandidateProposal.model_validate(doc)
                if digest is None:
                    raise CandidateRejected("schema_invalid")
                receipt_id = hashlib_sha(
                    "candidate-receipt", {"boundary": context.attempt_key, "digest": digest}
                )
                queue = self.ledger._queues.setdefault(context.semantic_boundary[:3], {})
                if receipt_id in queue or receipt_id in self.ledger._completed or len(queue) >= 32:
                    raise CandidateRejected("queue_depth_exhausted")
                receipt = canonical_bytes(
                    {
                        "schema_version": "secure-memory-candidate-receipt/v1",
                        "proposal_id": candidate.proposal_id,
                        "proposal_digest": canonical_sha256("candidate-proposal", candidate),
                        "receipt_id": receipt_id,
                    }
                )
                queue[receipt_id] = context.attempt_key
                self.ledger._attempts[context.attempt_key] = (digest, receipt, None)
                self.ledger._proposals[candidate.proposal_id] = (
                    context.semantic_boundary,
                    digest,
                    receipt,
                )
                return receipt
            except CandidateRejected as exc:
                self.ledger._attempts[context.attempt_key] = (digest, None, str(exc))
                if str(exc) not in {"forbidden_field"} and digest is not None:
                    self.ledger.schema_rejections.append(
                        {"proposal_digest": digest, "reason": str(exc)}
                    )
                raise
            except (TypeError, ValueError, ValidationError, UnicodeError) as exc:
                self.ledger._attempts[context.attempt_key] = (digest, None, "schema_invalid")
                if digest is not None:
                    self.ledger.schema_rejections.append(
                        {"proposal_digest": digest, "reason": "schema_invalid"}
                    )
                raise CandidateRejected("schema_invalid") from exc

    def complete(self, *, context: CandidateContext, receipt_id: str) -> None:
        with self.ledger._lock:
            self.ledger.complete(context, receipt_id)

    @staticmethod
    def _limits(doc: Mapping[str, Any]) -> None:
        if len(canonical_bytes(doc)) > MAX_CANONICAL_PAYLOAD_BYTES:
            raise CandidateRejected("payload_too_large")
        refs = doc.get("source_refs")
        if isinstance(refs, (list, tuple)) and len(refs) > 16:
            raise CandidateRejected("too_many_source_refs")
        value = doc.get("statement_utf8_base64")
        if not isinstance(value, str):
            raise CandidateRejected("schema_invalid")
        try:
            decoded = base64.b64decode(value, validate=True)
            decoded.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise CandidateRejected("schema_invalid") from exc
        if len(decoded) > 2048:
            raise CandidateRejected("statement_too_long")


def hashlib_sha(domain: str, value: Any) -> str:
    return canonical_sha256(domain, value)


def _forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            (isinstance(k, str) and k.casefold() in FORBIDDEN_TRUST_KEYS) or _forbidden(v)
            for k, v in value.items()
        )
    return isinstance(value, (list, tuple)) and any(_forbidden(v) for v in value)
