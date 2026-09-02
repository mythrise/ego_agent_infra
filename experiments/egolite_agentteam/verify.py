"""Offline verifier for an EgoLite live model-team acceptance directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from integrations.agentteams.model_gateway import sha256_bytes


EXPECTED_ROLES = {"research-pi", "scout", "experiment-architect", "reviewer"}


def verify_bundle(root: Path) -> Dict[str, Any]:
    errors = []
    try:
        checksums = json.loads((root / "SHA256SUMS.json").read_text(encoding="utf-8"))
        acceptance = json.loads((root / "acceptance.json").read_text(encoding="utf-8"))
        control = json.loads((root / "control-plane.json").read_text(encoding="utf-8"))
        research_os = json.loads((root / "research-os.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        return {"verified": False, "errors": ["bundle manifest unreadable: %s" % error]}

    actual = {
        path.relative_to(root).as_posix(): sha256_bytes(path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS.json"
    }
    if set(actual) != set(checksums):
        errors.append("checksum file set does not match bundle file set")
    mismatches = sorted(
        path for path in set(actual) & set(checksums) if actual[path] != checksums[path]
    )
    if mismatches:
        errors.append("checksum mismatch: %s" % ", ".join(mismatches))
    if acceptance.get("schema") != "egoagentos.egolite-model-team-acceptance/v1":
        errors.append("acceptance schema mismatch")
    if acceptance.get("structural_acceptance") != "PASS":
        errors.append("structural acceptance is not PASS")
    if acceptance.get("provider", {}).get("credential_persisted") is not False:
        errors.append("credential persistence assertion is not false")
    output = acceptance.get("output", {})
    if set(output.get("distinct_roles", [])) != EXPECTED_ROLES:
        errors.append("model role set is incomplete")
    if output.get("model_call_count") != len(EXPECTED_ROLES):
        errors.append("model call count is not four")
    if output.get("research_matrix_cells") != 165:
        errors.append("research matrix does not contain the frozen 165 cells")
    if output.get("resource_review") != "PASS":
        errors.append("independent resource review did not pass")
    if output.get("focus_compact_count") != len(EXPECTED_ROLES):
        errors.append("model roles did not all compact private focus memory")
    compile_result = research_os.get("compile", {})
    if compile_result.get("matrix", {}).get("cell_count") != 165:
        errors.append("research-os matrix artifact is incomplete")
    focus = research_os.get("focus_receipts", {})
    if set(focus) != EXPECTED_ROLES:
        errors.append("research-os focus receipt set is incomplete")
    for role, receipt in focus.items():
        if receipt.get("local", {}).get("truth_class") != "LIVE_LOCAL":
            errors.append("%s focus memory is not LIVE_LOCAL" % role)
        if receipt.get("local", {}).get("compacted") is not True:
            errors.append("%s focus memory was not compacted" % role)
        if receipt.get("remote", {}).get("truth_class") != "NOT_CONFIGURED":
            errors.append("%s remote memory truth boundary changed" % role)
    truth = acceptance.get("truth_boundary", {})
    expected_truth = {
        "external_model_calls": "LIVE",
        "control_plane_replay": "LIVE_LOCAL",
        "ego_workload_metrics": "SYNTHETIC_FIXTURE",
        "official_agentteams_controller": "NOT_RUN",
        "matrix_transport": "NOT_RUN",
        "physical_gpu": "NOT_RUN",
    }
    if truth != expected_truth:
        errors.append("truth boundary changed or is incomplete")
    completed = control.get("completed", {}).get("task", {})
    if completed.get("stage") != "COMPLETED":
        errors.append("control plane did not complete")
    if completed.get("gate_result", {}).get("status") != "pass":
        errors.append("control-plane evidence gate did not pass")
    if control.get("events", {}).get("chain_valid") is not True:
        errors.append("control-plane event chain is invalid")
    receipt_roles = {path.stem for path in (root / "receipts").glob("*.json")}
    if receipt_roles != EXPECTED_ROLES:
        errors.append("receipt role set is incomplete")

    return {
        "schema": "egoagentos.egolite-model-team-verification/v1",
        "verified": not errors,
        "errors": errors,
        "files_verified": len(actual),
        "trace_id": acceptance.get("trace_id"),
        "truth_boundary": truth,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    result = verify_bundle(args.bundle.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
