#!/usr/bin/env python3
"""Fail-fast repository checks used before building the GOAI submission ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Dict, List

from build_submission import included_files


ROOT = Path(__file__).resolve().parents[1]
SEMIFINAL_PROOF_PATH = ROOT / "submission" / "evidence" / "semifinal-local-proof.json"
SEMIFINAL_PROOF_CHECKSUM = SEMIFINAL_PROOF_PATH.with_suffix(".sha256")
LIVE_LOCAL_PROOF_PATH = (
    ROOT / "submission" / "evidence" / "agentteams-live-local-proof.json"
)
LIVE_LOCAL_PROOF_CHECKSUM = LIVE_LOCAL_PROOF_PATH.with_suffix(".sha256")
REQUIRED_AGENT_FIELDS = (
    "id:",
    "name:",
    "role:",
    "agentteams_role:",
    "capabilities:",
    "inputs:",
    "outputs:",
    "dependencies:",
    "decision_boundary:",
    "trace:",
)
REQUIRED_SKILL_SECTIONS = (
    "## Contract",
    "## Failure and safety",
    "## Verification and reuse",
)
SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
REQUIRED_DELIVERABLES = (
    ".dockerignore",
    ".env.example",
    ".github/workflows/ci.yml",
    ".github/workflows/pages.yml",
    "LICENSE",
    "Makefile",
    "README.md",
    "apps/agentteams_bridge/migrations/postgres/001_bridge_control_plane.sql",
    "apps/agentteams_bridge/postgres_store.py",
    "apps/api/event_stream.py",
    "apps/api/migrations/postgres/001_control_plane.sql",
    "apps/api/migrations/postgres/002_ledger_boundaries.sql",
    "apps/api/polardb_preflight.py",
    "apps/api/postgres_store.py",
    "benchmarks/artifacts/2026-08-29-local-cpu.json",
    "benchmarks/artifacts/2026-08-29-local-cpu.md",
    "benchmarks/artifacts/2026-08-29-local-cpu.sha256",
    "benchmarks/schemas/agentteams-rxp-trace-v1.schema.json",
    "benchmarks/trace-contract.md",
    "contracts/approval-token-v1.json",
    "deploy/postgres/agentteams_bridge_security.sql",
    "deploy/postgres/security_roles.sql",
    "docker-compose.yml",
    "docs/agentteams-live-runbook.md",
    "docs/evidence/postgres-local-proof-2026-08-29.md",
    "docs/judge-feedback-implementation.md",
    "docs/polardb-live-acceptance-runbook.md",
    "docs/postgres-recovery-runbook.md",
    "docs/semifinal-scorecard.md",
    "docs/protocols/RXP.md",
    "docs/openapi.json",
    "experiments/fashion_mnist_amp/README.md",
    "experiments/fashion_mnist_amp/config.json",
    "experiments/fashion_mnist_amp/contract.py",
    "experiments/fashion_mnist_amp/run.py",
    "experiments/fashion_mnist_amp/verify.py",
    "integrations/agentteams/benchmark_adapter.py",
    "integrations/agentteams/official-contract.lock.json",
    "mcp_servers/pyproject.toml",
    "mcp_servers/uv.lock",
    "pyproject.toml",
    "protocols/rxp/cli.py",
    "protocols/rxp/schemas/rxp-decision-v1.schema.json",
    "protocols/rxp/schemas/rxp-evidence-v1.schema.json",
    "protocols/rxp/schemas/rxp-grant-v1.schema.json",
    "protocols/rxp/schemas/rxp-intent-v1.schema.json",
    "protocols/rxp/schemas/rxp-matrix-ledger-v1.schema.json",
    "protocols/rxp/schemas/rxp-matrix-plan-v1.schema.json",
    "protocols/rxp/schemas/rxp-receipt-v1.schema.json",
    "requirements-api.lock",
    "scripts/build_semifinal_proof.py",
    "scripts/freeze_live_local_proof.py",
    "semifinal_acceptance/README.md",
    "semifinal_acceptance/bundle.py",
    "semifinal_acceptance/cli.py",
    "semifinal_acceptance/schemas/semifinal-acceptance-v1.schema.json",
    "skill_runtime/handlers.py",
    "skill_runtime/registry.py",
    "submission/EgoAgentOS_GOAI_Agent_Infra_初赛方案.pdf",
    "submission/EgoAgentOS_GOAI_Agent_Infra_初赛方案.pptx",
    "submission/EgoAgentOS_GOAI_Agent_Infra_复赛方案.pdf",
    "submission/EgoAgentOS_GOAI_Agent_Infra_复赛方案.pptx",
    "submission/demo-script-8min.md",
    "submission/evidence/semifinal-local-proof.json",
    "submission/evidence/semifinal-local-proof.sha256",
    "submission/evidence/agentteams-live-local-proof.json",
    "submission/evidence/agentteams-live-local-proof.sha256",
    "submission/project-summary-zh.txt",
    "submission/screenshots/semifinal-rxp-cockpit.png",
    "submission/semifinal-evidence-index.md",
    "submission/semifinal-submission-checklist.md",
    "submission/verification-report.md",
    "uv.lock",
)


def check(condition: bool, message: str, failures: List[str]) -> None:
    if not condition:
        failures.append(message)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def validate_agents(failures: List[str]) -> None:
    identity_files = sorted(
        path for path in (ROOT / "agents").glob("*.yaml") if path.name != "README.md"
    )
    check(len(identity_files) == 7, "expected exactly seven Agent identity YAML files", failures)
    for path in identity_files:
        content = read_text(path)
        for field in REQUIRED_AGENT_FIELDS:
            check(field in content, "%s missing %s" % (path, field), failures)


def validate_skills(failures: List[str]) -> None:
    skill_files = sorted((ROOT / "skills").glob("*/SKILL.md"))
    check(len(skill_files) == 6, "expected exactly six core Skill packages", failures)
    for path in skill_files:
        content = read_text(path)
        check(content.startswith("---\n"), "%s missing YAML frontmatter" % path, failures)
        check("name:" in content and "description:" in content, "%s frontmatter incomplete" % path, failures)
        for heading in REQUIRED_SKILL_SECTIONS:
            check(heading in content, "%s missing section %s" % (path, heading), failures)
        manifest = path.parent / "egoagentos.skill.yaml"
        check(manifest.exists(), "%s missing EgoAgentOS extension manifest" % path.parent, failures)


def validate_fixtures(failures: List[str]) -> None:
    fixtures = sorted((ROOT / "examples" / "egolite" / "fixtures").glob("*.json"))
    check(bool(fixtures), "no EgoLite JSON fixtures found", failures)
    for path in fixtures:
        payload = json.loads(read_text(path))
        check(payload.get("synthetic") is True, "%s is not explicitly synthetic" % path, failures)


def validate_summary(failures: List[str]) -> None:
    path = ROOT / "submission" / "project-summary-zh.txt"
    content = read_text(path)
    character_count = len(content.strip())
    check(character_count <= 500, "project summary exceeds 500 characters", failures)
    check("synthetic" in content, "project summary must disclose synthetic evidence", failures)


def validate_truth_labels(failures: List[str]) -> None:
    ledger = read_text(ROOT / "docs" / "claims-evidence.md")
    for phrase in ("8×RTX 4090 experiment ran", "AgentTeams Matrix collaboration is live", "Nacos Skill is published"):
        check(phrase in ledger, "claims ledger omits boundary: %s" % phrase, failures)


def validate_shared_contracts(failures: List[str]) -> None:
    contract_path = ROOT / "contracts" / "approval-token-v1.json"
    check(contract_path.exists(), "shared approval-token contract is missing", failures)
    if contract_path.exists():
        contract = json.loads(read_text(contract_path))
        check(contract.get("token", {}).get("prefix") == "egoap1", "approval prefix mismatch", failures)
        fields = contract.get("gpu_launch", {}).get("payload_fields", [])
        check("config_sha256" in fields, "GPU approval payload is not config-bound", failures)
    check(
        (ROOT / "apps" / "api" / "fixtures" / "egolite-mcp-launch.yaml").exists(),
        "config bytes referenced by the GPU approval contract are missing",
        failures,
    )


def validate_required_deliverables(failures: List[str]) -> None:
    packaged = {path.relative_to(ROOT).as_posix() for path in included_files()}
    for relative in REQUIRED_DELIVERABLES:
        path = ROOT / relative
        check(path.is_file(), "required deliverable is missing: %s" % relative, failures)
        check(not path.is_symlink(), "required deliverable cannot be a symlink: %s" % relative, failures)
        check(relative in packaged, "required deliverable is excluded from ZIP: %s" % relative, failures)

    pptx = ROOT / "submission" / "EgoAgentOS_GOAI_Agent_Infra_初赛方案.pptx"
    pdf = ROOT / "submission" / "EgoAgentOS_GOAI_Agent_Infra_初赛方案.pdf"
    if pptx.is_file():
        check(pptx.stat().st_size > 10_000, "proposal PPTX is unexpectedly small", failures)
        check(pptx.read_bytes()[:2] == b"PK", "proposal PPTX signature is invalid", failures)
    if pdf.is_file():
        check(pdf.stat().st_size > 10_000, "proposal PDF is unexpectedly small", failures)
        check(pdf.read_bytes()[:5] == b"%PDF-", "proposal PDF signature is invalid", failures)


def validate_semifinal_artifacts(failures: List[str]) -> None:
    pptx = ROOT / "submission" / "EgoAgentOS_GOAI_Agent_Infra_复赛方案.pptx"
    pdf = ROOT / "submission" / "EgoAgentOS_GOAI_Agent_Infra_复赛方案.pdf"
    screenshot = ROOT / "submission" / "screenshots" / "semifinal-rxp-cockpit.png"
    index = ROOT / "submission" / "semifinal-evidence-index.md"
    if pptx.is_file():
        check(pptx.stat().st_size > 500_000, "semifinal PPTX is unexpectedly small", failures)
        check(pptx.read_bytes()[:2] == b"PK", "semifinal PPTX signature is invalid", failures)
        try:
            with zipfile.ZipFile(pptx) as archive:
                names = set(archive.namelist())
                slide_count = sum(
                    bool(re.fullmatch(r"ppt/slides/slide\d+\.xml", name)) for name in names
                )
                note_count = sum(
                    bool(re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", name))
                    for name in names
                )
                check(slide_count == 16, "semifinal PPTX must contain 16 slides", failures)
                check(note_count == 16, "semifinal PPTX must contain 16 notes slides", failures)
        except zipfile.BadZipFile:
            failures.append("semifinal PPTX is not a readable OOXML archive")
    if pdf.is_file():
        check(pdf.stat().st_size > 500_000, "semifinal PDF is unexpectedly small", failures)
        check(pdf.read_bytes()[:5] == b"%PDF-", "semifinal PDF signature is invalid", failures)
    if screenshot.is_file():
        check(screenshot.stat().st_size > 100_000, "semifinal screenshot is unexpectedly small", failures)
        check(
            screenshot.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n",
            "semifinal screenshot signature is invalid",
            failures,
        )
    if index.is_file():
        content = read_text(index)
        for path, label in (
            (pptx, "Semifinal PPTX"),
            (pdf, "Semifinal PDF"),
            (screenshot, "Cockpit screenshot"),
        ):
            if path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                check(digest in content, "%s SHA-256 is stale in evidence index" % label, failures)


def validate_semifinal_proof(failures: List[str]) -> None:
    if not SEMIFINAL_PROOF_PATH.is_file() or not SEMIFINAL_PROOF_CHECKSUM.is_file():
        return
    payload = SEMIFINAL_PROOF_PATH.read_bytes()
    expected_checksum = "%s  %s\n" % (
        hashlib.sha256(payload).hexdigest(),
        SEMIFINAL_PROOF_PATH.name,
    )
    check(
        SEMIFINAL_PROOF_CHECKSUM.read_text(encoding="ascii") == expected_checksum,
        "semifinal local proof SHA-256 is stale or invalid",
        failures,
    )
    try:
        proof = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        failures.append("semifinal local proof is not valid UTF-8 JSON")
        return
    check(
        proof.get("schema_version") == "egoagentos.semifinal-local-proof/v1",
        "semifinal local proof schema_version mismatch",
        failures,
    )
    local = proof.get("local_executable_proofs", {})
    check(local.get("rxp", {}).get("status") == "PASS", "RXP proof is not PASS", failures)
    skill = local.get("skill_runtime", {})
    check(skill.get("status") == "PASS", "Skill runtime proof is not PASS", failures)
    check(
        skill.get("research_plan_invocation", {}).get("trace", {}).get("status") == "PASS",
        "ResearchPlan invocation proof is not PASS",
        failures,
    )
    boundaries = proof.get("external_runtime_boundaries", {})
    agentteams = boundaries.get("live_agentteams", {})
    check(agentteams.get("status") == "SKIP", "live AgentTeams must remain SKIP", failures)
    check(
        agentteams.get("verification") == "UNVERIFIED",
        "live AgentTeams must remain UNVERIFIED",
        failures,
    )
    for name in ("polardb_deployment", "pitr_restore", "application_docker_image"):
        boundary = boundaries.get(name, {})
        check(boundary.get("status") == "NOT_RUN", "%s must remain NOT_RUN" % name, failures)
        check(
            boundary.get("verification") == "UNVERIFIED",
            "%s must remain UNVERIFIED" % name,
            failures,
        )
    indexes = proof.get("committed_evidence_indexes", {})
    evidence_files = list(indexes.get("benchmark", {}).get("files", []))
    for name in ("agentteams_contract_lock", "postgresql_local_contract"):
        item = indexes.get(name, {}).get("file")
        if isinstance(item, dict):
            evidence_files.append(item)
    for item in evidence_files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            failures.append("semifinal proof contains an invalid evidence file record")
            continue
        path = (ROOT / item["path"]).resolve()
        if ROOT.resolve() not in path.parents or not path.is_file() or path.is_symlink():
            failures.append("semifinal proof evidence path is unsafe or missing: %s" % item["path"])
            continue
        content = path.read_bytes()
        check(len(content) == item.get("bytes"), "%s byte count mismatch" % item["path"], failures)
        check(
            hashlib.sha256(content).hexdigest() == item.get("sha256"),
            "%s SHA-256 mismatch" % item["path"],
            failures,
        )


def validate_live_local_proof(failures: List[str]) -> None:
    if not LIVE_LOCAL_PROOF_PATH.is_file() or not LIVE_LOCAL_PROOF_CHECKSUM.is_file():
        return
    payload = LIVE_LOCAL_PROOF_PATH.read_bytes()
    expected_checksum = "%s  %s\n" % (
        hashlib.sha256(payload).hexdigest(),
        LIVE_LOCAL_PROOF_PATH.name,
    )
    check(
        LIVE_LOCAL_PROOF_CHECKSUM.read_text(encoding="ascii") == expected_checksum,
        "AgentTeams live-local proof SHA-256 is stale or invalid",
        failures,
    )
    try:
        proof = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        failures.append("AgentTeams live-local proof is not valid UTF-8 JSON")
        return
    check(
        proof.get("schema_version")
        == "egoagentos.agentteams-live-local-proof/v1",
        "AgentTeams live-local proof schema_version mismatch",
        failures,
    )
    check(proof.get("status") == "PASS", "AgentTeams live-local proof is not PASS", failures)
    truth = proof.get("truth_boundary", {})
    check(
        truth.get("official_agentteams_infrastructure") == "LIVE_LOCAL",
        "official AgentTeams infrastructure is not LIVE_LOCAL",
        failures,
    )
    check(
        truth.get("official_scientific_workflow") == "NOT_RUN",
        "scientific workflow must remain NOT_RUN",
        failures,
    )
    check(
        truth.get("physical_gpu") == "NOT_ATTACHED",
        "physical GPU must remain NOT_ATTACHED",
        failures,
    )
    checks = proof.get("checks", {})
    check(
        len(checks) == 10 and all(value is True for value in checks.values()),
        "AgentTeams live-local invariant set is incomplete or failed",
        failures,
    )


def scan_secrets(failures: List[str]) -> None:
    """Scan exactly the files that can enter the deterministic submission ZIP."""

    for path in sorted(set(included_files())):
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".pdf", ".pptx", ".zip", ".woff", ".woff2"}:
            continue
        try:
            content = read_text(path)
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                failures.append("possible committed secret in %s (%s)" % (path, pattern.pattern))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args()

    failures: List[str] = []
    validate_agents(failures)
    validate_skills(failures)
    validate_fixtures(failures)
    validate_summary(failures)
    validate_truth_labels(failures)
    validate_shared_contracts(failures)
    validate_required_deliverables(failures)
    validate_semifinal_artifacts(failures)
    validate_semifinal_proof(failures)
    validate_live_local_proof(failures)
    scan_secrets(failures)

    result: Dict[str, object] = {
        "status": "PASS" if not failures else "FAIL",
        "checks": 11,
        "failures": failures,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("submission verification: %s" % result["status"])
        for failure in failures:
            print("- %s" % failure)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
