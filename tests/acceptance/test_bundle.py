from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from semifinal_acceptance import AcceptanceError, build_bundle, verify_bundle
from tests.acceptance.conftest import build_acceptance_source


def test_build_is_deterministic_and_offline_replayable(
    acceptance_source: Path, tmp_path: Path
) -> None:
    first = tmp_path / "bundle-a"
    second = tmp_path / "bundle-b"
    built_a = build_bundle(acceptance_source, first)
    built_b = build_bundle(acceptance_source, second)
    assert built_a["bundle_root"] == built_b["bundle_root"]
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    verified = verify_bundle(first)
    assert verified["status"] == "CONTRACT_PASS_ORIGIN_UNVERIFIED"
    assert verified["contract_status"] == "PASS"
    assert verified["external_origin_status"] == "UNVERIFIED"
    assert verified["live_claim_allowed"] is False
    assert verified["mvp_coverage"] == "8/14"
    assert verified["full_release_status"] == "NOT_EVALUATED"
    assert verified["external_calls"] == 0


def test_artifact_tamper_breaks_bundle_replay(
    acceptance_source: Path, tmp_path: Path
) -> None:
    bundle = tmp_path / "bundle"
    build_bundle(acceptance_source, bundle)
    target = bundle / "artifacts/metrics/raw.jsonl"
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(AcceptanceError, match="artifact digest mismatch"):
        verify_bundle(bundle)


def test_secret_is_rejected_before_bundle_creation(tmp_path: Path) -> None:
    source = build_acceptance_source(tmp_path / "source")
    (source / "leaked.json").write_text(
        '{"approval_token":"this-is-a-live-secret-value"}\n', encoding="utf-8"
    )
    with pytest.raises(AcceptanceError, match="possible secret JSON field"):
        build_bundle(source, tmp_path / "bundle")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "nan", "filter", "summary"])
def test_raw_evidence_failures_are_fail_closed(tmp_path: Path, mutation: str) -> None:
    source = build_acceptance_source(tmp_path / mutation / "source")
    raw = source / "metrics/raw.jsonl"
    lines = raw.read_text(encoding="utf-8").splitlines()
    if mutation == "missing":
        raw.write_text(lines[0] + "\n", encoding="utf-8")
    elif mutation == "duplicate":
        duplicate = json.loads(lines[0])
        duplicate["record_id"] = "r3"
        raw.write_text("\n".join(lines + [json.dumps(duplicate)]) + "\n", encoding="utf-8")
    elif mutation == "nan":
        gpu = source / "runtime/gpu-metrics.jsonl"
        gpu.write_text(gpu.read_text(encoding="utf-8").replace('"power_w":100', '"power_w":NaN'), encoding="utf-8")
    elif mutation == "filter":
        record = json.loads(lines[0])
        record["included"] = False
        record["filter_id"] = "post-hoc-filter"
        raw.write_text(json.dumps(record) + "\n" + lines[1] + "\n", encoding="utf-8")
    else:
        summary = source / "metrics/summary.json"
        value = json.loads(summary.read_text(encoding="utf-8"))
        value["sum_scaled"] = 999
        summary.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(AcceptanceError):
        build_bundle(source, tmp_path / mutation / "bundle")


def test_mvp_cannot_promote_itself_to_full_release(tmp_path: Path) -> None:
    source = build_acceptance_source(tmp_path / "source")
    descriptor_path = source / "acceptance-input.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["corpus"]["full_release_status"] = "PASS"
    descriptor_path.write_text(json.dumps(descriptor) + "\n", encoding="utf-8")
    with pytest.raises(AcceptanceError, match="corpus/gate declaration mismatch"):
        build_bundle(source, tmp_path / "bundle")


def test_cli_build_and_verify(acceptance_source: Path, tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "semifinal_acceptance",
            "build",
            "--source",
            str(acceptance_source),
            "--output",
            str(bundle),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    verify = subprocess.run(
        [
            sys.executable,
            "-m",
            "semifinal_acceptance",
            "verify",
            "--bundle",
            str(bundle),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stderr
    assert json.loads(verify.stdout)["full_release_status"] == "NOT_EVALUATED"


def test_undeclared_bundle_file_is_rejected(acceptance_source: Path, tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    build_bundle(acceptance_source, bundle)
    extra = bundle / "artifacts/extra.txt"
    extra.write_text("not in the manifest", encoding="utf-8")
    with pytest.raises(AcceptanceError, match="undeclared artifacts"):
        verify_bundle(bundle)


def test_operator_assertion_cannot_promote_external_origin(tmp_path: Path) -> None:
    source = build_acceptance_source(tmp_path / "source")
    descriptor_path = source / "acceptance-input.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["truth_boundary"]["external_origin_authentication"] = "VERIFIED"
    descriptor_path.write_text(json.dumps(descriptor) + "\n", encoding="utf-8")
    with pytest.raises(AcceptanceError, match="UNVERIFIED_OPERATOR_ASSERTION"):
        build_bundle(source, tmp_path / "bundle")


def test_reused_agentteams_raw_response_is_rejected(tmp_path: Path) -> None:
    source = build_acceptance_source(tmp_path / "source")
    receipts_path = source / "agentteams/receipts.json"
    document = json.loads(receipts_path.read_text(encoding="utf-8"))
    first, second = document["receipts"][:2]
    second["raw_file"] = first["raw_file"]
    second["raw_sha256"] = first["raw_sha256"]
    receipts_path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    with pytest.raises(AcceptanceError, match="unique raw response bytes"):
        build_bundle(source, tmp_path / "bundle")


def test_untyped_matrix_content_is_rejected(tmp_path: Path) -> None:
    source = build_acceptance_source(tmp_path / "source")
    matrix_path = source / "agentteams/matrix-events.jsonl"
    records = [json.loads(line) for line in matrix_path.read_text(encoding="utf-8").splitlines()]
    records[0]["content"] = {}
    matrix_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    with pytest.raises(AcceptanceError, match="typed trace binding"):
        build_bundle(source, tmp_path / "bundle")


def test_decision_must_equal_recomputed_raw_metric_policy(tmp_path: Path) -> None:
    source = build_acceptance_source(tmp_path / "source")
    decision_path = source / "decision/decision.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    decision["verdict"] = "REJECT"
    decision_path.write_text(json.dumps(decision) + "\n", encoding="utf-8")
    with pytest.raises(AcceptanceError, match="recomputed raw metrics"):
        build_bundle(source, tmp_path / "bundle")


def test_negative_gpu_telemetry_is_rejected(tmp_path: Path) -> None:
    source = build_acceptance_source(tmp_path / "source")
    gpu_path = source / "runtime/gpu-metrics.jsonl"
    records = [json.loads(line) for line in gpu_path.read_text(encoding="utf-8").splitlines()]
    records[0]["power_w"] = -1
    gpu_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    with pytest.raises(AcceptanceError, match="power_w must be non-negative"):
        build_bundle(source, tmp_path / "bundle")
