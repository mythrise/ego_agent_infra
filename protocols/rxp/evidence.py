"""Deterministic evidence completeness and independence assessment."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .canonical import digest_document, merkle_root
from .models import DIGEST_PATTERN, Evidence, GateAssessment

REQUIRED_EVIDENCE_TYPES = (
    "code",
    "config",
    "dataset_manifest",
    "log",
    "metric",
    "trace",
    "review",
)


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return set()
    return set(value)


def evidence_gate(records: Iterable[Evidence]) -> GateAssessment:
    evidence = tuple(records)
    digests = tuple(sorted(digest_document(record) for record in evidence))
    present = tuple(sorted({record.evidence_type for record in evidence}))
    missing = tuple(sorted(set(REQUIRED_EVIDENCE_TYPES) - set(present)))
    reasons: list[str] = []
    if missing:
        reasons.append("missing required evidence: " + ", ".join(missing))

    receipt_digests = {record.receipt_digest for record in evidence}
    if len(receipt_digests) > 1:
        reasons.append("evidence spans multiple receipts")
    evidence_ids = [record.evidence_id for record in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        reasons.append("duplicate evidence_id")

    metrics = [record for record in evidence if record.evidence_type == "metric"]
    valid_metric = any(
        record.claims.get("deterministic") is True
        and record.claims.get("summary_only") is False
        and isinstance(record.claims.get("raw_data_digest"), str)
        and re.fullmatch(DIGEST_PATTERN, record.claims["raw_data_digest"])
        for record in metrics
    )
    if metrics and not valid_metric:
        reasons.append("metric evidence requires deterministic raw data")

    producers = {
        record.producer_id for record in evidence if record.evidence_type != "review"
    }
    independent_reviewer = None
    for review in (record for record in evidence if record.evidence_type == "review"):
        reviewed = _string_set(review.claims.get("reviewed_producers"))
        if (
            review.claims.get("reviewer_id") == review.producer_id
            and review.claims.get("independent") is True
            and review.claims.get("verdict") == "PASS"
            and review.producer_id not in producers
            and bool(reviewed)
            and producers.issubset(reviewed)
        ):
            independent_reviewer = review.producer_id
            break
    if any(record.evidence_type == "review" for record in evidence) and not independent_reviewer:
        reasons.append("independent PASS review must cover all non-review producers")

    return GateAssessment(
        status="FAIL" if reasons else "PASS",
        required_types=REQUIRED_EVIDENCE_TYPES,
        present_types=present,
        missing_types=missing,
        evidence_digests=digests,
        evidence_root=merkle_root(digests),
        independent_reviewer=independent_reviewer,
        reasons=tuple(reasons),
    )
