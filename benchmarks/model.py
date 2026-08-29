"""Stable data model and canonical serialization for benchmark observations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from benchmarks import BENCHMARK_VERSION


def canonical_json(value: Any) -> str:
    """Serialize JSON with the same byte representation on every supported runtime."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    title: str
    description: str
    capability: str
    expected_invariant: str


@dataclass
class Observation:
    profile: str
    scenario_id: str
    repetition: int
    seed: int
    status: str
    latency_ms: float
    operation_count: int
    task_completed: Optional[bool] = None
    unsafe_action_blocked: Optional[bool] = None
    approval_bypass_succeeded: Optional[bool] = None
    exactly_once: Optional[bool] = None
    trace_completeness: Optional[float] = None
    evidence_completeness: Optional[float] = None
    recovered: Optional[bool] = None
    mttr_ms: Optional[float] = None
    reproducible: Optional[bool] = None
    hash_agreement: Optional[bool] = None
    dynamically_routed: Optional[bool] = None
    trace_root: Optional[str] = None
    evidence_root: Optional[str] = None
    external_cost_usd: Optional[float] = None
    assertions: List[str] = field(default_factory=list)
    implementation_path: str = ""
    reason: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["latency_ms"] = round(self.latency_ms, 6)
        if self.mttr_ms is not None:
            data["mttr_ms"] = round(self.mttr_ms, 6)
        return data

    @classmethod
    def skipped(
        cls,
        profile: str,
        scenario: Scenario,
        repetition: int,
        seed: int,
        reason: str,
        implementation_path: str,
    ) -> "Observation":
        return cls(
            profile=profile,
            scenario_id=scenario.id,
            repetition=repetition,
            seed=seed,
            status="skip",
            latency_ms=0.0,
            operation_count=0,
            reason=reason,
            implementation_path=implementation_path,
        )


@dataclass(frozen=True)
class Corpus:
    benchmark: str
    corpus_version: str
    default_repetitions: int
    master_seed: int
    scenarios: List[Scenario]
    digest: str


def load_corpus(path: Optional[Path] = None) -> Corpus:
    corpus_path = path or Path(__file__).parent / "corpus" / "v1" / "scenarios.json"
    raw = json.loads(corpus_path.read_text(encoding="utf-8"))
    if raw.get("benchmark") != BENCHMARK_VERSION:
        raise ValueError("Unsupported benchmark corpus: %r" % raw.get("benchmark"))
    scenarios = [Scenario(**entry) for entry in raw["scenarios"]]
    ids = [scenario.id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("Scenario ids must be unique")
    required = {
        "happy_path",
        "plan_conflict",
        "worker_timeout_reassign",
        "stale_context",
        "token_replay",
        "token_expiry",
        "token_scope_mismatch",
        "concurrent_duplicate",
        "crash_recovery",
        "evidence_tamper",
        "forged_reviewer",
        "skill_version_rollback",
        "matrix_cherry_pick",
        "matrix_missing_seed",
    }
    missing = required - set(ids)
    if missing:
        raise ValueError("Corpus is missing required scenarios: %s" % sorted(missing))
    return Corpus(
        benchmark=raw["benchmark"],
        corpus_version=raw["corpus_version"],
        default_repetitions=int(raw["default_repetitions"]),
        master_seed=int(raw["master_seed"]),
        scenarios=scenarios,
        digest=canonical_sha256(raw),
    )


def derive_seed(master_seed: int, scenario_id: str, repetition: int) -> int:
    material = "%d:%s:%d" % (master_seed, scenario_id, repetition)
    return int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:8], 16)
