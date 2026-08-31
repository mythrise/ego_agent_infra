"""Untrusted candidate proposal RPC validation and immutable quota accounting."""

from __future__ import annotations

import base64
import binascii
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, MutableMapping, Tuple

from pydantic import ValidationError

from ..canonical import canonical_bytes, canonical_sha256
from ..models import CandidateProposal


MAX_PROPOSALS_PER_TURN = 16
MAX_PROPOSALS_PER_PROBLEM = 32
MAX_PROPOSALS_PER_CAMPAIGN = 128
MAX_CANONICAL_PAYLOAD_BYTES = 64 * 1024
MAX_STATEMENT_UTF8_BYTES = 2048
MAX_SOURCE_REFS = 16
MAX_QUEUE_DEPTH = 32
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
    """An untrusted candidate did not meet schema, ownership, or quota rules."""


@dataclass
class CandidateQuotaLedger:
    """Append-only reservation counters; rejected retries never refund capacity."""

    _turns: Counter = field(default_factory=Counter)
    _problems: Counter = field(default_factory=Counter)
    _campaigns: Counter = field(default_factory=Counter)
    _queues: Counter = field(default_factory=Counter)
    schema_rejections: List[Dict[str, str]] = field(default_factory=list)
    scanner_rejections: List[Dict[str, Any]] = field(default_factory=list)

    def reserve(self, campaign_id: str, task_id: str, turn_id: str) -> None:
        turn_key = (campaign_id, task_id, turn_id)
        problem_key = (campaign_id, task_id)
        if self._turns[turn_key] >= MAX_PROPOSALS_PER_TURN:
            raise CandidateRejected("turn_quota_exhausted")
        if self._problems[problem_key] >= MAX_PROPOSALS_PER_PROBLEM:
            raise CandidateRejected("problem_quota_exhausted")
        if self._campaigns[campaign_id] >= MAX_PROPOSALS_PER_CAMPAIGN:
            raise CandidateRejected("campaign_quota_exhausted")
        if self._queues[campaign_id] >= MAX_QUEUE_DEPTH:
            raise CandidateRejected("queue_depth_exhausted")
        self._turns[turn_key] += 1
        self._problems[problem_key] += 1
        self._campaigns[campaign_id] += 1
        self._queues[campaign_id] += 1

    def queue(self, campaign_id: str) -> int:
        return self._queues[campaign_id]

    def complete(self, campaign_id: str) -> None:
        if self._queues[campaign_id] <= 0:
            raise ValueError("cannot complete an empty candidate queue")
        self._queues[campaign_id] -= 1

    def record_schema_rejection(self, proposal_digest: str, reason: str) -> None:
        self.schema_rejections.append({"proposal_digest": proposal_digest, "reason": reason})

    def record_scanner_rejection(self, source_class: str, reason: str) -> None:
        self.scanner_rejections.append(
            {"source_class": source_class, "reason": reason, "count": 1}
        )


class CandidateRpc:
    """Accept a bounded untrusted CandidateProposal and return an opaque receipt."""

    def __init__(self, *, ledger: CandidateQuotaLedger, campaign_id: str, arm: str, tenant: str) -> None:
        self.ledger = ledger
        self._campaign_id = campaign_id
        self._arm = arm
        self._tenant = tenant
        self._receipts: MutableMapping[Tuple[str, str, str, str], Tuple[str, bytes]] = {}

    def propose(
        self,
        proposal: Mapping[str, Any],
        *,
        turn_id: str,
        arm: str,
        tenant: str,
    ) -> bytes:
        if arm != self._arm:
            raise CandidateRejected("arm_mismatch")
        if tenant != self._tenant:
            raise CandidateRejected("tenant_mismatch")
        if not isinstance(proposal, Mapping):
            raise CandidateRejected("invalid_proposal")
        if _has_forbidden_key(proposal):
            self.ledger.record_scanner_rejection("proposal", "forbidden_field")
            raise CandidateRejected("forbidden_field")
        try:
            proposal_document = dict(proposal)
            proposal_digest = canonical_sha256("candidate-proposal", proposal_document)
        except (TypeError, ValueError) as exc:
            raise CandidateRejected("invalid_proposal") from exc

        proposal_id = proposal_document.get("proposal_id")
        task_id = proposal_document.get("task_id")
        if not isinstance(proposal_id, str) or not proposal_id or not isinstance(task_id, str) or not task_id:
            self.ledger.record_schema_rejection(proposal_digest, "invalid_identity")
            raise CandidateRejected("invalid_proposal")
        receipt_key = (self._campaign_id, arm, tenant, proposal_id)
        prior = self._receipts.get(receipt_key)
        if prior is not None:
            if prior[0] == proposal_digest:
                return prior[1]
            raise CandidateRejected("idempotency_reuse_with_different_bytes")

        self.ledger.reserve(self._campaign_id, task_id, turn_id)
        try:
            self._validate_payload_limits(proposal_document)
            candidate = CandidateProposal.model_validate(proposal_document)
        except CandidateRejected as exc:
            self.ledger.record_schema_rejection(proposal_digest, str(exc))
            raise
        except (TypeError, ValidationError, ValueError) as exc:
            self.ledger.record_schema_rejection(proposal_digest, "schema_invalid")
            raise CandidateRejected("schema_invalid") from exc

        receipt = canonical_bytes(
            {
                "campaign_id": self._campaign_id,
                "proposal_digest": canonical_sha256("candidate-proposal", candidate),
                "proposal_id": candidate.proposal_id,
                "schema_version": "secure-memory-candidate-receipt/v1",
            }
        )
        self._receipts[receipt_key] = (proposal_digest, receipt)
        return receipt

    @staticmethod
    def _validate_payload_limits(proposal: Mapping[str, Any]) -> None:
        if len(canonical_bytes(proposal)) > MAX_CANONICAL_PAYLOAD_BYTES:
            raise CandidateRejected("payload_too_large")
        refs = proposal.get("source_refs")
        if isinstance(refs, (list, tuple)) and len(refs) > MAX_SOURCE_REFS:
            raise CandidateRejected("too_many_source_refs")
        statement = proposal.get("statement_utf8_base64")
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
        for key, child in value.items():
            if isinstance(key, str) and key.casefold() in FORBIDDEN_TRUST_KEYS:
                return True
            if _has_forbidden_key(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_has_forbidden_key(child) for child in value)
    return False
