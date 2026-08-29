"""Allowlisted deterministic handlers for the semifinal Skill runtime."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Dict, Mapping


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_digest(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value


def research_plan(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if payload.get("goal_frozen") is not True:
        raise ValueError("goal must be frozen before planning")
    goal_digest = _require_digest(payload, "goal_digest")
    context_digest = _require_digest(payload, "context_digest")
    hypotheses = payload.get("hypotheses")
    if not isinstance(hypotheses, list) or not hypotheses or not all(
        isinstance(item, str) and item.strip() for item in hypotheses
    ):
        raise ValueError("at least one falsifiable hypothesis is required")
    arms = payload.get("arms")
    if not isinstance(arms, list) or "baseline" not in arms or len(set(arms)) < 2:
        raise ValueError("plan needs a baseline and at least one distinct candidate arm")
    seeds = payload.get("seeds")
    if not isinstance(seeds, list) or not seeds or not all(isinstance(seed, int) for seed in seeds):
        raise ValueError("one or more integer seeds are required")
    estimated = payload.get("estimated_gpu_hours")
    budget = payload.get("budget_gpu_hours")
    if not isinstance(estimated, (int, float)) or not isinstance(budget, (int, float)):
        raise ValueError("estimated_gpu_hours and budget_gpu_hours must be numeric")
    if estimated < 0 or budget < 0 or estimated > budget:
        raise ValueError("estimated GPU hours exceed the frozen budget")
    rollback = payload.get("rollback_target")
    if not isinstance(rollback, str) or not rollback.strip():
        raise ValueError("rollback_target is required")
    metrics = payload.get("metrics")
    required = {"name", "direction", "unit", "threshold", "split", "aggregation"}
    if not isinstance(metrics, list) or not metrics:
        raise ValueError("at least one acceptance metric is required")
    for metric in metrics:
        if not isinstance(metric, Mapping) or not required.issubset(metric):
            raise ValueError("every metric must define name/direction/unit/threshold/split/aggregation")
        if metric["direction"] not in {"higher_better", "lower_better"}:
            raise ValueError("metric direction must be higher_better or lower_better")
    plan = {
        "goal_digest": goal_digest,
        "context_digest": context_digest,
        "hypotheses": hypotheses,
        "arms": arms,
        "seeds": seeds,
        "metrics": metrics,
        "estimated_gpu_hours": estimated,
        "budget_gpu_hours": budget,
        "rollback_target": rollback,
    }
    return {
        "status": "READY_FOR_INDEPENDENT_REVIEW",
        "plan": plan,
        "plan_digest": _digest(plan),
    }


def dataset_manifest(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    dataset_id = payload.get("dataset_id")
    version = payload.get("version")
    files = payload.get("files")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("dataset_id is required")
    if not isinstance(version, str) or not version:
        raise ValueError("dataset version is required")
    if not isinstance(files, list) or not files:
        raise ValueError("files must contain at least one pre-hashed file record")
    normalized: list[Dict[str, Any]] = []
    seen = set()
    for item in files:
        if not isinstance(item, Mapping):
            raise ValueError("every file record must be an object")
        path = item.get("path")
        size = item.get("size")
        digest = item.get("sha256")
        if not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/"):
            raise ValueError("file paths must be non-empty, relative, and traversal-free")
        if path in seen:
            raise ValueError("duplicate file path")
        if not isinstance(size, int) or size < 0:
            raise ValueError("file size must be a non-negative integer")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError("file sha256 must be a lowercase SHA-256")
        seen.add(path)
        normalized.append({"path": path, "size": size, "sha256": digest})
    manifest = {
        "dataset_id": dataset_id,
        "version": version,
        "files": sorted(normalized, key=lambda x: str(x["path"])),
    }
    return {"manifest": manifest, "manifest_digest": _digest(manifest)}


def evidence_gate(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    required = payload.get("required_kinds")
    evidence = payload.get("evidence")
    reviewer = payload.get("reviewer")
    executor = payload.get("executor")
    if not isinstance(required, list) or not required or not all(isinstance(x, str) for x in required):
        raise ValueError("required_kinds must be a non-empty string list")
    if not isinstance(evidence, list):
        raise ValueError("evidence must be a list")
    if not isinstance(reviewer, str) or not reviewer:
        raise ValueError("reviewer is required")
    if reviewer == executor:
        raise ValueError("reviewer must be independent from executor")
    present = set()
    ledger = []
    for item in evidence:
        if not isinstance(item, Mapping):
            raise ValueError("evidence records must be objects")
        kind = item.get("kind")
        digest = item.get("digest")
        if not isinstance(kind, str) or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError("evidence records require kind and lowercase SHA-256 digest")
        present.add(kind)
        ledger.append({"kind": kind, "digest": digest, "producer": item.get("producer")})
    missing = sorted(set(required) - present)
    if missing:
        raise ValueError("missing required evidence kinds: " + ", ".join(missing))
    review = {
        "required": sorted(set(required)),
        "present": sorted(present),
        "reviewer": reviewer,
        "executor": executor,
        "ledger_digest": _digest(
            sorted(ledger, key=lambda x: (str(x["kind"]), str(x["digest"])))
        ),
    }
    return {"status": "PASS", "review": review, "review_digest": _digest(review)}


def default_handlers() -> Dict[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]]:
    return {
        "research-plan": research_plan,
        "dataset-manifest": dataset_manifest,
        "evidence-gate": evidence_gate,
    }
