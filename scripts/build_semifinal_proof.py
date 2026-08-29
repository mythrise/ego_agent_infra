#!/usr/bin/env python3
"""Build the deterministic, truth-labelled GOAI semifinal local proof bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from protocols.rxp import (  # noqa: E402
    GrantSigner,
    MatrixLedgerDocument,
    demo_bytes,
    verify_grant_signatures,
    verify_ledger_document,
)
from protocols.rxp.demo import DEMO_HMAC_KEY, DEMO_KEY_ID  # noqa: E402
from protocols.rxp.schema import check_schemas  # noqa: E402
from skill_runtime import SkillRegistry, default_handlers  # noqa: E402


PROOF_PATH = ROOT / "submission" / "evidence" / "semifinal-local-proof.json"
CHECKSUM_PATH = PROOF_PATH.with_suffix(".sha256")
BENCHMARK_JSON = ROOT / "benchmarks" / "artifacts" / "2026-08-29-local-cpu.json"
BENCHMARK_MARKDOWN = ROOT / "benchmarks" / "artifacts" / "2026-08-29-local-cpu.md"
BENCHMARK_CHECKSUM = ROOT / "benchmarks" / "artifacts" / "2026-08-29-local-cpu.sha256"
AGENTTEAMS_LOCK = ROOT / "integrations" / "agentteams" / "official-contract.lock.json"
POSTGRES_PROOF = ROOT / "docs" / "evidence" / "postgres-local-proof-2026-08-29.md"
TRACE_CONTRACT = ROOT / "benchmarks" / "trace-contract.md"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _tracked(path: Path) -> bool:
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative(path)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def file_evidence(path: Path) -> Dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("proof input must be a regular non-symlink file: %s" % path)
    if not _tracked(path):
        raise ValueError("proof input must already be committed/tracked: %s" % path)
    payload = path.read_bytes()
    return {
        "path": relative(path),
        "bytes": len(payload),
        "sha256": sha256_bytes(payload),
        "tracked": True,
    }


def _verify_sha256_manifest(path: Path, expected_files: Sequence[Path]) -> None:
    entries: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            digest, filename = line.split(None, 1)
        except ValueError as error:
            raise ValueError("invalid SHA-256 manifest line") from error
        filename = filename.strip().lstrip("*")
        if not _SHA256.fullmatch(digest):
            raise ValueError("invalid SHA-256 digest in manifest")
        entries[filename] = digest
    for expected in expected_files:
        if entries.get(expected.name) != sha256_file(expected):
            raise ValueError("benchmark checksum mismatch for %s" % expected.name)


def rxp_proof() -> Dict[str, Any]:
    first = demo_bytes()
    second = demo_bytes()
    if first != second:
        raise ValueError("RXP demo is not byte deterministic")
    ledger = MatrixLedgerDocument.model_validate_json(first)
    verify_ledger_document(ledger)
    verify_grant_signatures(
        ledger,
        GrantSigner(DEMO_HMAC_KEY, key_id=DEMO_KEY_ID),
    )
    stale_schemas = check_schemas()
    if stale_schemas:
        raise ValueError("RXP schemas are stale: %s" % stale_schemas)
    return {
        "status": "PASS",
        "verification_scope": "LOCAL_SYNTHETIC_DETERMINISTIC_FIXTURE",
        "command_equivalent": "rxp demo && rxp verify --demo-key",
        "demo_sha256": sha256_bytes(first),
        "byte_replay_equal": True,
        "signatures_verified": True,
        "schemas_current": True,
        "matrix_id": ledger.matrix_id,
        "matrix_root": ledger.root,
        "completeness": ledger.completeness,
        "expected_cells": ledger.expected_cell_count,
        "decided_cells": ledger.decided_cell_count,
        "entry_count": ledger.entry_count,
        "truth_boundary": "Public synthetic demo key and fixture; no physical/GPU experiment claim.",
    }


def research_plan_payload() -> Dict[str, Any]:
    return {
        "goal_frozen": True,
        "goal_digest": "a" * 64,
        "context_digest": "b" * 64,
        "hypotheses": [
            "candidate improves throughput without exceeding the frozen error budget"
        ],
        "arms": ["baseline", "candidate"],
        "seeds": [11, 22, 33],
        "estimated_gpu_hours": 2,
        "budget_gpu_hours": 3,
        "rollback_target": "git:semifinal-proof-fixture",
        "metrics": [
            {
                "name": "throughput",
                "direction": "higher_better",
                "unit": "fps",
                "threshold": 10,
                "split": "synthetic-test-v1",
                "aggregation": "mean",
            }
        ],
    }


def skill_proof() -> Dict[str, Any]:
    registry = SkillRegistry.discover(ROOT / "skills", default_handlers())
    catalog = list(registry.catalog())
    plan_descriptor = next(item for item in catalog if item["name"] == "research-plan")
    correlation_id = "semifinal-local-proof:research-plan:v1"
    first = registry.invoke(
        "research-plan",
        research_plan_payload(),
        correlation_id,
        expected_version=str(plan_descriptor["version"]),
        expected_package_digest=str(plan_descriptor["package_digest"]),
    )
    second = registry.invoke(
        "research-plan",
        research_plan_payload(),
        correlation_id,
        expected_version=str(plan_descriptor["version"]),
        expected_package_digest=str(plan_descriptor["package_digest"]),
    )
    if first != second:
        raise ValueError("ResearchPlan invocation is not deterministic")
    catalog_digest = sha256_bytes(
        json.dumps(
            catalog,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return {
        "status": "PASS",
        "verification_scope": "LOCAL_ALLOWLISTED_SKILL_RUNTIME",
        "catalog_count": len(catalog),
        "executable_count": sum(bool(item["executable"]) for item in catalog),
        "catalog_digest": catalog_digest,
        "catalog": catalog,
        "research_plan_invocation": first,
        "repeat_invocation_equal": True,
        "truth_boundary": (
            "In-process deterministic registry/handler proof; no Nacos publication or "
            "distributed rollout claim."
        ),
    }


def benchmark_proof() -> Dict[str, Any]:
    _verify_sha256_manifest(
        BENCHMARK_CHECKSUM,
        (BENCHMARK_JSON, BENCHMARK_MARKDOWN),
    )
    payload = json.loads(BENCHMARK_JSON.read_text(encoding="utf-8"))
    profiles: List[Dict[str, Any]] = []
    for name, summary in sorted(payload["summary"]["profiles"].items()):
        profiles.append(
            {
                "name": name,
                "trials": summary["trials"],
                "passed": summary["passed"],
                "failed": summary["failed"],
                "errors": summary["errors"],
                "skipped": summary["skipped"],
                "coverage": summary["coverage"],
                "scenario_clusters": summary["scenario_success"]["n"],
                "scenario_clusters_passed": summary["scenario_success"]["successes"],
            }
        )
    target = next(item for item in profiles if item["name"] == "agentteams-rxp-target")
    if target["passed"] != 0 or target["skipped"] != target["trials"]:
        raise ValueError("committed target benchmark no longer has the declared SKIP boundary")
    return {
        "status": "VERIFIED_COMMITTED_ARTIFACT",
        "verification_scope": payload["environment"]["execution_class"],
        "benchmark": payload["benchmark"],
        "corpus_version": payload["corpus_version"],
        "corpus_digest": payload["corpus_digest"],
        "semantic_digest": payload["semantic_digest"],
        "master_seed": payload["configuration"]["master_seed"],
        "repetitions": payload["configuration"]["repetitions"],
        "profiles": profiles,
        "files": [
            file_evidence(BENCHMARK_JSON),
            file_evidence(BENCHMARK_MARKDOWN),
            file_evidence(BENCHMARK_CHECKSUM),
            file_evidence(TRACE_CONTRACT),
        ],
        "truth_boundary": (
            "Committed local synthetic CPU control-plane benchmark; target live AgentTeams "
            "trials are all SKIP and are never counted as PASS."
        ),
    }


def contract_lock_proof() -> Dict[str, Any]:
    payload = json.loads(AGENTTEAMS_LOCK.read_text(encoding="utf-8"))
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("AgentTeams contract lock has no pinned artifacts")
    return {
        "status": "VERIFIED_COMMITTED_PIN_SET",
        "file": file_evidence(AGENTTEAMS_LOCK),
        "schema": payload["schema"],
        "repository": payload["repository"],
        "stable_tag": payload["stable"]["tag"],
        "stable_commit": payload["stable"]["commit"],
        "main_commit": payload["main"]["commit"],
        "api_version": payload["apiVersion"],
        "pinned_artifact_count": len(artifacts),
        "pinned_artifacts_digest": sha256_bytes(
            json.dumps(
                artifacts,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ),
        "truth_boundary": "Source/API pin integrity only; not evidence of a live AgentTeams run.",
    }


def postgres_proof() -> Dict[str, Any]:
    content = POSTGRES_PROOF.read_text(encoding="utf-8")
    version = re.search(r"PostgreSQL ([0-9.]+)", content)
    passed = re.search(r"\b(\d+) passed\b", content)
    image = re.search(r"\bpostgres:[0-9A-Za-z._-]+\b", content)
    if version is None or passed is None or image is None:
        raise ValueError("PostgreSQL proof summary is incomplete")
    return {
        "status": "VERIFIED_FROM_COMMITTED_LOCAL_PROOF",
        "file": file_evidence(POSTGRES_PROOF),
        "engine": "PostgreSQL",
        "version": version.group(1),
        "database_image_observed_in_committed_proof": image.group(0),
        "tests_passed": int(passed.group(1)),
        "proof_reexecuted_by_builder": False,
        "truth_boundary": (
            "Index of a committed disposable local PostgreSQL proof; this builder does not "
            "re-run Docker, PolarDB, backup, failover, or PITR."
        ),
    }


def build_proof() -> Dict[str, Any]:
    return {
        "schema_version": "egoagentos.semifinal-local-proof/v1",
        "proof_id": "egoagentos-semifinal-local-proof-2026-08-29",
        "determinism": {
            "serialization": "UTF-8 JSON, sorted keys, two-space indent, NaN forbidden",
            "timestamps": "omitted; proof is content-derived",
            "self_checksum": relative(CHECKSUM_PATH),
        },
        "local_executable_proofs": {
            "rxp": rxp_proof(),
            "skill_runtime": skill_proof(),
        },
        "committed_evidence_indexes": {
            "benchmark": benchmark_proof(),
            "agentteams_contract_lock": contract_lock_proof(),
            "postgresql_local_contract": postgres_proof(),
        },
        "external_runtime_boundaries": {
            "live_agentteams": {
                "status": "SKIP",
                "verification": "UNVERIFIED",
                "reason": "No live AgentTeams Controller/team/workers and bound scenario credentials were used by this proof build.",
            },
            "polardb_deployment": {
                "status": "NOT_RUN",
                "verification": "UNVERIFIED",
                "reason": "No PolarDB account, endpoint, or deployment drill was used.",
            },
            "pitr_restore": {
                "status": "NOT_RUN",
                "verification": "UNVERIFIED",
                "reason": "No backup policy export, restore job, or measured RPO/RTO exists.",
            },
            "application_docker_image": {
                "status": "NOT_RUN",
                "verification": "UNVERIFIED",
                "reason": "The EgoAgentOS application image was not built or run by this proof builder.",
            },
        },
        "overall": {
            "status": "PASS_WITH_EXPLICIT_SKIPS",
            "scope": "DETERMINISTIC_LOCAL_SEMIFINAL_PROOF",
            "release_claim": False,
            "truth_boundary": (
                "PASS covers only the executable local RXP/Skill proofs and integrity of "
                "committed evidence indexes. External runtime gaps remain explicit."
            ),
        },
    }


def proof_bytes() -> bytes:
    return canonical_bytes(build_proof())


def checksum_bytes(payload: bytes) -> bytes:
    return ("%s  %s\n" % (sha256_bytes(payload), PROOF_PATH.name)).encode("ascii")


def write_proof() -> None:
    payload = proof_bytes()
    checksum = checksum_bytes(payload)
    PROOF_PATH.parent.mkdir(parents=True, exist_ok=True)
    proof_temporary = PROOF_PATH.with_suffix(".json.tmp")
    checksum_temporary = CHECKSUM_PATH.with_suffix(".sha256.tmp")
    proof_temporary.write_bytes(payload)
    checksum_temporary.write_bytes(checksum)
    proof_temporary.replace(PROOF_PATH)
    checksum_temporary.replace(CHECKSUM_PATH)


def check_proof() -> List[str]:
    failures = []
    expected = proof_bytes()
    if not PROOF_PATH.is_file() or PROOF_PATH.read_bytes() != expected:
        failures.append("semifinal local proof JSON is missing or stale")
    expected_checksum = checksum_bytes(expected)
    if not CHECKSUM_PATH.is_file() or CHECKSUM_PATH.read_bytes() != expected_checksum:
        failures.append("semifinal local proof SHA-256 is missing or stale")
    return failures


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if committed proof is stale")
    args = parser.parse_args(list(argv) if argv else None)
    if args.check:
        failures = check_proof()
        for failure in failures:
            print(failure, file=sys.stderr)
        if failures:
            return 1
        print("semifinal local proof: PASS (content and checksum current)")
        return 0
    write_proof()
    print("wrote %s" % PROOF_PATH)
    print("sha256 %s" % sha256_file(PROOF_PATH))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
