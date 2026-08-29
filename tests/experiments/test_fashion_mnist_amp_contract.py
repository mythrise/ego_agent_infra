from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from experiments.fashion_mnist_amp.contract import (
    CONFIG_SCHEMA,
    RAW_SCHEMA,
    ContractError,
    add_raw_digest,
    canonical_sha256,
    evaluate_raw_result,
    file_manifest,
    validate_config,
)
from experiments.fashion_mnist_amp.verify import main as verify_main
from experiments.fashion_mnist_amp.run import _GPUTelemetry, _acceptance_metric_artifacts


def config_fixture() -> dict[str, object]:
    return {
        "schema": CONFIG_SCHEMA,
        "workload_id": "fashion-mnist-amp-v1",
        "dataset": {
            "name": "FashionMNIST",
            "source": "https://github.com/zalandoresearch/fashion-mnist",
            "license": "MIT",
            "synthetic": False,
            "download": False,
            "train_samples": 32,
            "eval_samples": 32,
        },
        "model": {
            "architecture": "tiny-cnn-v1",
            "classes": 10,
            "epochs": 1,
            "train_batch_size": 32,
            "eval_batch_size": 32,
            "learning_rate": 0.001,
        },
        "comparison": {
            "baseline": "cuda-fp32",
            "candidate": "cuda-amp-fp16",
            "warmup_repetitions": 1,
            "latency_repetitions": 5,
            "max_accuracy_degradation": 0.005,
            "min_latency_speedup": 0.0,
        },
        "budget": {
            "gpu_count": 1,
            "max_duration_seconds": 900,
            "max_gpu_hours": 0.25,
            "max_download_bytes": 1024,
        },
        "governance": {
            "risk_level": "R2",
            "human_approval_required": True,
            "max_physical_launches": 1,
            "independent_review_required": True,
        },
        "telemetry": {
            "nvidia_smi_path": "/usr/bin/nvidia-smi",
            "sampling": "stage_boundaries",
        },
        "determinism": {
            "cublas_workspace_config": ":4096:8",
            "deterministic_algorithms": True,
            "cudnn_benchmark": False,
            "tf32": False,
        },
        "seed": 42,
    }


def raw_fixture() -> dict[str, object]:
    config = validate_config(config_fixture())
    samples = [
        {
            "sample_id": index,
            "target": index % 10,
            "baseline_pred": index % 10,
            "candidate_pred": index % 10,
        }
        for index in range(32)
    ]
    return add_raw_digest(
        {
            "schema": RAW_SCHEMA,
            "execution_mode": "real_cuda",
            "synthetic": False,
            "physical_launch_count": 1,
            "run_id": "run-live-0001",
            "physical_launch_id": "gpu-launch-0001",
            "cpu_fallback_used": False,
            "workload_id": "fashion-mnist-amp-v1",
            "config": config,
            "config_sha256": canonical_sha256(config),
            "config_file_sha256": "1" * 64,
            "dataset_manifest_sha256": "2" * 64,
            "git_commit": "a" * 40,
            "git_commit_sha256": hashlib.sha256(("a" * 40).encode("ascii")).hexdigest(),
            "environment_lock_sha256": "7" * 64,
            "approval_receipt_sha256": "4" * 64,
            "agentteams_receipt_sha256": "5" * 64,
            "matrix_plan_sha256": "6" * 64,
            "binding_artifacts": {
                "environment_lock": {
                    "basename": "environment.lock",
                    "bytes": 10,
                    "sha256": "7" * 64,
                },
                "approval_receipt": {
                    "basename": "approval.json",
                    "bytes": 10,
                    "sha256": "4" * 64,
                },
                "agentteams_receipt": {
                    "basename": "agentteams.json",
                    "bytes": 10,
                    "sha256": "5" * 64,
                },
                "matrix_plan": {
                    "basename": "matrix.json",
                    "bytes": 10,
                    "sha256": "6" * 64,
                },
            },
            "device": {
                "type": "cuda",
                "cuda_available": True,
                "visible_device_count": 1,
                "name": "test-gpu",
            },
            "determinism": {
                "cublas_workspace_config": ":4096:8",
                "deterministic_algorithms": True,
                "cudnn_benchmark": False,
                "tf32": False,
            },
            "duration_seconds": 60.0,
            "samples": samples,
            "latency_ms": {
                "baseline": [10.0, 10.2, 9.9, 10.1, 10.0],
                "candidate": [7.0, 7.2, 7.1, 6.9, 7.0],
            },
            "max_memory_bytes": {"baseline": 1024, "candidate": 768},
            "trained_model_sha256": "8" * 64,
        }
    )


def resign(raw: dict[str, object]) -> dict[str, object]:
    payload = {key: value for key, value in raw.items() if key != "raw_sha256"}
    return add_raw_digest(payload)


def test_self_asserted_real_cuda_bytes_recompute_keep_without_live_claim() -> None:
    raw = raw_fixture()
    result = evaluate_raw_result(raw, expected_config_sha256=str(raw["config_sha256"]))

    assert result["contract_gate_status"] == "PASS"
    assert result["verification_status"] == "CONTRACT_PASS_ORIGIN_UNVERIFIED"
    assert result["external_origin_status"] == "UNVERIFIED"
    assert result["live_claim_allowed"] is False
    assert result["decision"] == "KEEP"
    assert result["sample_count"] == 32
    assert result["baseline_accuracy"] == 1.0
    assert result["candidate_accuracy"] == 1.0
    assert result["latency_rule_pass"] is True
    assert result["observed_gpu_hours"] == pytest.approx(1 / 60)


def test_scientific_failure_is_reject_not_execution_failure() -> None:
    raw = raw_fixture()
    rows = copy.deepcopy(raw["samples"])
    assert isinstance(rows, list)
    for row in rows[:4]:
        assert isinstance(row, dict)
        row["candidate_pred"] = (int(row["target"]) + 1) % 10
    raw["samples"] = rows
    raw = resign(raw)

    result = evaluate_raw_result(raw)

    assert result["contract_gate_status"] == "PASS"
    assert result["live_claim_allowed"] is False
    assert result["decision"] == "REJECT"
    assert result["accuracy_rule_pass"] is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("synthetic", True, "synthetic evidence"),
        ("execution_mode", "synthetic_local_process", "not real_cuda"),
        ("physical_launch_count", 2, "exactly one physical launch"),
        ("cpu_fallback_used", True, "CPU fallback"),
        ("duration_seconds", 901.0, "wall-time budget"),
    ],
)
def test_live_truth_and_budget_boundary_fail_closed(
    field: str, value: object, message: str
) -> None:
    raw = raw_fixture()
    raw[field] = value
    raw = resign(raw)

    with pytest.raises(ContractError, match=message):
        evaluate_raw_result(raw)


def test_duplicate_samples_nan_and_digest_tampering_are_rejected() -> None:
    duplicate = raw_fixture()
    rows = copy.deepcopy(duplicate["samples"])
    assert isinstance(rows, list) and isinstance(rows[1], dict)
    rows[1]["sample_id"] = 0
    duplicate["samples"] = rows
    duplicate = resign(duplicate)
    with pytest.raises(ContractError, match="duplicate sample_id"):
        evaluate_raw_result(duplicate)

    nonfinite = raw_fixture()
    latency = copy.deepcopy(nonfinite["latency_ms"])
    assert isinstance(latency, dict) and isinstance(latency["candidate"], list)
    latency["candidate"][0] = float("nan")
    nonfinite["latency_ms"] = latency
    with pytest.raises(ContractError, match="NaN or infinity"):
        evaluate_raw_result(nonfinite)

    tampered = raw_fixture()
    tampered["duration_seconds"] = 61.0
    with pytest.raises(ContractError, match="raw evidence digest mismatch"):
        evaluate_raw_result(tampered)


def test_config_rejects_synthetic_multi_gpu_or_overspend() -> None:
    for mutation in (
        lambda value: value["dataset"].update({"synthetic": True}),
        lambda value: value["budget"].update({"gpu_count": 2}),
        lambda value: value["budget"].update({"max_gpu_hours": 0.5}),
    ):
        config = config_fixture()
        mutation(config)
        with pytest.raises(ContractError):
            validate_config(config)


def test_artifact_manifest_is_relative_deterministic_and_content_bound(tmp_path: Path) -> None:
    first = tmp_path / "b.json"
    second = tmp_path / "a.json"
    first.write_text(json.dumps({"b": 2}), encoding="utf-8")
    second.write_text(json.dumps({"a": 1}), encoding="utf-8")

    left = file_manifest(tmp_path, [first, second])
    right = file_manifest(tmp_path, [second, first])

    assert left == right
    assert [item["path"] for item in left["files"]] == ["a.json", "b.json"]
    second.write_text(json.dumps({"a": 9}), encoding="utf-8")
    assert file_manifest(tmp_path, [first, second]) != left


def test_offline_verifier_cli_writes_once_and_rejects_tamper(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw_path = tmp_path / "raw.json"
    output_path = tmp_path / "decision.json"
    raw_path.write_text(json.dumps(raw_fixture()), encoding="utf-8")

    assert verify_main([str(raw_path), "--output", str(output_path)]) == 0
    assert json.loads(output_path.read_text(encoding="utf-8"))["result"]["decision"] == "KEEP"
    assert verify_main([str(raw_path), "--output", str(output_path)]) == 2
    assert "output_exists" in capsys.readouterr().err

    tampered = raw_fixture()
    tampered["duration_seconds"] = 61.0
    raw_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert verify_main([str(raw_path)]) == 2
    assert "raw evidence digest mismatch" in capsys.readouterr().err


def test_gpu_telemetry_uses_fixed_argv_and_one_job_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "nvidia-smi"
    executable.write_bytes(b"fixture executable")
    calls: list[tuple[list[str], dict[str, object]]] = []

    class Result:
        stdout = b"GPU-test-uuid, NVIDIA Test GPU, 42, 128, 75.5\n"

    def fake_run(command: list[str], **kwargs: object) -> Result:
        calls.append((command, kwargs))
        return Result()

    monkeypatch.setattr("experiments.fashion_mnist_amp.run.subprocess.run", fake_run)
    telemetry = _GPUTelemetry(
        executable=str(executable),
        device_binding="GPU-test-uuid",
        job_id="gpu-launch-0001",
    )
    telemetry.start()
    telemetry.sample()
    records = telemetry.finish()

    assert len(records) == 3
    assert [record["sequence"] for record in records] == [1, 2, 3]
    assert {record["gpu_uuid"] for record in records} == {"GPU-test-uuid"}
    assert {record["job_id"] for record in records} == {"gpu-launch-0001"}
    assert records[0]["memory_used_bytes"] == 128 * 1024 * 1024
    assert all(call[0][-1] == "--id=GPU-test-uuid" for call in calls)
    assert all(call[1]["shell"] is False for call in calls)


def test_predictions_project_to_complete_acceptance_metric_matrix() -> None:
    raw = raw_fixture()
    raw_bytes, summary, contract = _acceptance_metric_artifacts(raw)
    records = [json.loads(line) for line in raw_bytes.splitlines()]

    assert len(records) == 64
    assert len({(record["sample_id"], record["cell_id"]) for record in records}) == 64
    assert {record["cell_id"] for record in records} == {
        "cell-baseline-fp32",
        "cell-candidate-amp",
    }
    assert summary["sum_scaled"] == 64
    assert summary["mean_scaled_fraction"] == "1/1"
    assert summary["raw_metrics_sha256"] == "sha256:%s" % hashlib.sha256(raw_bytes).hexdigest()
    assert len(contract["sample_ids"]) == 32
    assert len(contract["matrix_cells"]) == 2
