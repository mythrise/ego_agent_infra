"""Execute the one-GPU Fashion-MNIST FP32 versus AMP acceptance workload.

Torch and torchvision are imported lazily so the contract verifier remains usable in
the repository's CPU-only development environment. This executable refuses CPU
fallback and refuses to run when more than one CUDA device is visible.
"""

from __future__ import annotations

import argparse
import csv
from fractions import Fraction
import hashlib
import io
import json
import os
import random
import re
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from .contract import (
    ContractError,
    RAW_SCHEMA,
    add_raw_digest,
    canonical_bytes,
    canonical_sha256,
    evaluate_raw_result,
    file_manifest,
    load_and_validate_config,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_identifier(value: str, name: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", value) is None:
        raise ContractError(f"{name} must match the bounded identifier contract")
    return value


def _bound_file_record(value: str, name: str, *, json_object: bool) -> Dict[str, Any]:
    """Read a caller-supplied binding artifact instead of trusting a digest argument."""

    source = Path(value)
    if source.is_symlink():
        raise ContractError(f"{name} must not be a symlink")
    try:
        resolved = source.resolve(strict=True)
    except OSError as error:
        raise ContractError(f"{name} is missing") from error
    if not resolved.is_file():
        raise ContractError(f"{name} must be a regular file")
    payload = resolved.read_bytes()
    if not payload or len(payload) > 16 * 1024 * 1024:
        raise ContractError(f"{name} must contain between 1 byte and 16 MiB")
    if json_object:
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContractError(f"{name} must be a UTF-8 JSON object") from error
        if not isinstance(parsed, dict):
            raise ContractError(f"{name} must be a UTF-8 JSON object")
    return {
        "basename": resolved.name,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _prepare_output(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.exists() and any(resolved.iterdir()):
        raise ContractError("output directory must be new or empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _tree_manifest(root: Path) -> Dict[str, Any]:
    resolved_root = root.resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        raise ContractError("dataset root does not exist after dataset initialization")
    entries: List[Dict[str, Any]] = []
    total_bytes = 0
    for path in sorted(resolved_root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ContractError("dataset tree contains a symlink")
        if not path.is_file():
            continue
        size = path.stat().st_size
        total_bytes += size
        entries.append(
            {
                "path": path.relative_to(resolved_root).as_posix(),
                "size": size,
                "sha256": _sha256_file(path),
            }
        )
    if not entries:
        raise ContractError("dataset tree contains no files")
    return {
        "schema": "egoagentos.dataset-tree/v1",
        "dataset": "FashionMNIST",
        "synthetic": False,
        "files": entries,
        "total_bytes": total_bytes,
    }


def _acceptance_metric_artifacts(raw: Dict[str, Any]) -> Tuple[bytes, Dict[str, Any], Dict[str, Any]]:
    """Project predictions into the semifinal bundle's exact sample-by-cell contract."""

    cells = (
        ("cell-baseline-fp32", "baseline_pred"),
        ("cell-candidate-amp", "candidate_pred"),
    )
    records: List[Dict[str, Any]] = []
    sample_ids: List[str] = []
    total = 0
    for row in raw["samples"]:
        sample_id = "fashion-mnist-test-%06d" % int(row["sample_id"])
        sample_ids.append(sample_id)
        for cell_id, prediction_field in cells:
            value = int(int(row[prediction_field]) == int(row["target"]))
            total += value
            records.append(
                {
                    "record_id": "%s:%s" % (sample_id, cell_id),
                    "sample_id": sample_id,
                    "cell_id": cell_id,
                    "metric_name": "classification_correct",
                    "value_scaled": value,
                    "included": True,
                    "filter_id": None,
                }
            )
    raw_bytes = b"".join(canonical_bytes(record) + b"\n" for record in records)
    mean = Fraction(total, len(records))
    summary = {
        "metric_name": "classification_correct",
        "scale": 1,
        "aggregation": "mean",
        "included_n": len(records),
        "filtered_n": 0,
        "sum_scaled": total,
        "mean_scaled_fraction": "%d/%d" % (mean.numerator, mean.denominator),
        "raw_metrics_sha256": "sha256:%s" % hashlib.sha256(raw_bytes).hexdigest(),
    }
    degradation_limit = Fraction(
        str(raw["config"]["comparison"]["max_accuracy_degradation"])
    )
    contract = {
        "metric_name": "classification_correct",
        "scale": 1,
        "aggregation": "mean",
        "sample_ids": sample_ids,
        "matrix_cells": [
            {"cell_id": "cell-baseline-fp32", "seed": int(raw["config"]["seed"])},
            {"cell_id": "cell-candidate-amp", "seed": int(raw["config"]["seed"])},
        ],
        "declared_filters": [],
        "decision_policy": {
            "kind": "candidate_noninferiority",
            "baseline_cell_id": "cell-baseline-fp32",
            "candidate_cell_id": "cell-candidate-amp",
            "max_degradation_scaled_fraction": "%d/%d"
            % (degradation_limit.numerator, degradation_limit.denominator),
            "pass_rationale_code": "ACCURACY_NONINFERIOR",
            "fail_rationale_code": "ACCURACY_DEGRADED",
        },
    }
    return raw_bytes, summary, contract


def _deadline_guard(started: float, max_seconds: int) -> None:
    if time.monotonic() - started > max_seconds:
        raise ContractError("wall-time budget exceeded during execution")


def _trace_event(
    events: List[Dict[str, Any]], event_type: str, payload: Dict[str, Any]
) -> None:
    previous_hash = events[-1]["event_hash"] if events else "0" * 64
    body = {
        "sequence": len(events) + 1,
        "event_type": event_type,
        "payload": payload,
        "previous_hash": previous_hash,
    }
    body["event_hash"] = canonical_sha256(body)
    events.append(body)


class _GPUTelemetry:
    """Sample one scheduler-bound GPU with fixed argv and fail closed on any gap."""

    def __init__(self, *, executable: str, device_binding: str, job_id: str):
        self.executable = executable
        self.device_binding = device_binding
        self.job_id = job_id
        self.records: List[Dict[str, Any]] = []

    def _sample(self) -> None:
        try:
            completed = subprocess.run(
                [
                    self.executable,
                    "--query-gpu=uuid,name,utilization.gpu,memory.used,power.draw",
                    "--format=csv,noheader,nounits",
                    "--id=%s" % self.device_binding,
                ],
                check=True,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ContractError("fixed nvidia-smi telemetry command failed") from error
        try:
            rows = list(csv.reader(io.StringIO(completed.stdout.decode("utf-8", "strict"))))
            if len(rows) != 1 or len(rows[0]) != 5:
                raise ValueError("unexpected nvidia-smi row shape")
            gpu_uuid, gpu_model, utilization, memory_mib, power = (
                value.strip() for value in rows[0]
            )
            record = {
                "sequence": len(self.records) + 1,
                "timestamp_ns": time.time_ns(),
                "job_id": self.job_id,
                "gpu_uuid": gpu_uuid,
                "gpu_model": gpu_model,
                "utilization_pct": float(utilization),
                "memory_used_bytes": int(float(memory_mib) * 1024 * 1024),
                "power_w": float(power),
            }
        except (UnicodeDecodeError, ValueError) as error:
            raise ContractError("nvidia-smi telemetry is malformed") from error
        if not gpu_uuid or not gpu_model:
            raise ContractError("nvidia-smi telemetry omitted GPU identity")
        self.records.append(record)

    def start(self) -> None:
        executable = Path(self.executable)
        if not executable.is_absolute():
            raise ContractError("fixed nvidia-smi executable is missing or unsafe")
        try:
            resolved = executable.resolve(strict=True)
        except OSError as error:
            raise ContractError("fixed nvidia-smi executable is missing or unsafe") from error
        if not resolved.is_file():
            raise ContractError("fixed nvidia-smi executable is missing or unsafe")
        self.executable = str(resolved)
        self._sample()

    def sample(self) -> None:
        self._sample()

    def finish(self) -> List[Dict[str, Any]]:
        # Capture a terminal sample on the main path. Stage-boundary calls above avoid
        # an unaccounted background process and still retain the job's physical timeline.
        self._sample()
        if len(self.records) < 2:
            raise ContractError("GPU telemetry requires at least two samples")
        timestamps = [int(record["timestamp_ns"]) for record in self.records]
        if timestamps != sorted(set(timestamps)):
            raise ContractError("GPU telemetry timestamps are not strictly increasing")
        identities = {(record["job_id"], record["gpu_uuid"]) for record in self.records}
        if len(identities) != 1:
            raise ContractError("GPU telemetry changed job or device identity")
        return self.records


def _run_torch_workload(
    config: Dict[str, Any], data_root: Path, started: float, physical_launch_id: str
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], bytes, List[Dict[str, Any]]]:
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "")
    visible_ids = [item.strip() for item in visible.split(",") if item.strip()]
    if len(visible_ids) != 1 or visible_ids[0] == "-1":
        raise ContractError("CUDA_VISIBLE_DEVICES must expose exactly one GPU")
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = str(
        config["determinism"]["cublas_workspace_config"]
    )
    telemetry = _GPUTelemetry(
        executable=str(config["telemetry"]["nvidia_smi_path"]),
        device_binding=visible_ids[0],
        job_id=physical_launch_id,
    )
    telemetry.start()

    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, Subset
        from torchvision import datasets, transforms
    except ImportError as error:
        raise ContractError("torch and torchvision are required on the live worker") from error

    if not torch.cuda.is_available():
        raise ContractError("CUDA is unavailable; CPU fallback is forbidden")
    if torch.cuda.device_count() != 1:
        raise ContractError("Torch must observe exactly one CUDA device")

    seed = int(config["seed"])
    random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False

    class TinyCNN(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.features = nn.Sequential(
                nn.Conv2d(1, 32, kernel_size=3, padding=1),
                nn.ReLU(),
                nn.MaxPool2d(2),
                nn.Conv2d(32, 64, kernel_size=3, padding=1),
                nn.ReLU(),
                nn.MaxPool2d(2),
            )
            self.classifier = nn.Sequential(
                nn.Flatten(),
                nn.Linear(64 * 7 * 7, 128),
                nn.ReLU(),
                nn.Linear(128, 10),
            )

        def forward(self, inputs: Any) -> Any:
            return self.classifier(self.features(inputs))

    transform = transforms.Compose(
        [transforms.ToTensor(), transforms.Normalize((0.2860,), (0.3530,))]
    )
    download = bool(config["dataset"]["download"])
    train_data = datasets.FashionMNIST(
        root=str(data_root), train=True, transform=transform, download=download
    )
    eval_data = datasets.FashionMNIST(
        root=str(data_root), train=False, transform=transform, download=download
    )
    train_count = int(config["dataset"]["train_samples"])
    eval_count = int(config["dataset"]["eval_samples"])
    train_subset = Subset(train_data, list(range(train_count)))
    eval_subset = Subset(eval_data, list(range(eval_count)))
    generator = torch.Generator().manual_seed(seed)
    train_loader = DataLoader(
        train_subset,
        batch_size=int(config["model"]["train_batch_size"]),
        shuffle=True,
        generator=generator,
        num_workers=0,
        drop_last=False,
    )
    eval_loader = DataLoader(
        eval_subset,
        batch_size=int(config["model"]["eval_batch_size"]),
        shuffle=False,
        num_workers=0,
        drop_last=False,
    )

    device = torch.device("cuda:0")
    model = TinyCNN().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=float(config["model"]["learning_rate"]))
    loss_function = nn.CrossEntropyLoss()
    events: List[Dict[str, Any]] = []
    _trace_event(events, "gpu.environment.verified", {"visible_device_count": 1})

    model.train()
    for epoch in range(int(config["model"]["epochs"])):
        for images, targets in train_loader:
            _deadline_guard(started, int(config["budget"]["max_duration_seconds"]))
            images = images.to(device, non_blocking=False)
            targets = targets.to(device, non_blocking=False)
            optimizer.zero_grad(set_to_none=True)
            logits = model(images)
            loss = loss_function(logits, targets)
            loss.backward()
            optimizer.step()
        telemetry.sample()
        _trace_event(events, "training.epoch.completed", {"epoch": epoch + 1})

    model_blob = bytearray(b"EGOAGENTOS-MODEL-STATE-V1\n")
    for name, tensor in sorted(model.state_dict().items()):
        value = tensor.detach().cpu().contiguous()
        name_bytes = name.encode("utf-8")
        tensor_bytes = value.numpy().tobytes(order="C")
        metadata = canonical_bytes(
            {"dtype": str(value.dtype), "shape": list(value.shape), "bytes": len(tensor_bytes)}
        )
        model_blob.extend(struct.pack(">I", len(name_bytes)))
        model_blob.extend(name_bytes)
        model_blob.extend(struct.pack(">I", len(metadata)))
        model_blob.extend(metadata)
        model_blob.extend(struct.pack(">Q", len(tensor_bytes)))
        model_blob.extend(tensor_bytes)
    model_state_bytes = bytes(model_blob)
    trained_model_sha256 = hashlib.sha256(model_state_bytes).hexdigest()
    _trace_event(events, "model.frozen", {"trained_model_sha256": trained_model_sha256})

    model.eval()
    samples: List[Dict[str, int]] = []
    sample_offset = 0
    with torch.inference_mode():
        for images, targets in eval_loader:
            _deadline_guard(started, int(config["budget"]["max_duration_seconds"]))
            images = images.to(device, non_blocking=False)
            baseline_predictions = model(images).argmax(dim=1).cpu().tolist()
            with torch.autocast(device_type="cuda", dtype=torch.float16):
                candidate_predictions = model(images).argmax(dim=1).cpu().tolist()
            target_values = targets.tolist()
            for index, target in enumerate(target_values):
                samples.append(
                    {
                        "sample_id": sample_offset + index,
                        "target": int(target),
                        "baseline_pred": int(baseline_predictions[index]),
                        "candidate_pred": int(candidate_predictions[index]),
                    }
                )
            sample_offset += len(target_values)
    _trace_event(events, "predictions.frozen", {"sample_count": len(samples)})
    telemetry.sample()

    first_images, _ = next(iter(eval_loader))
    benchmark_images = first_images.to(device, non_blocking=False)
    warmups = int(config["comparison"]["warmup_repetitions"])
    repetitions = int(config["comparison"]["latency_repetitions"])

    def measure(*, amp: bool) -> Tuple[List[float], int]:
        with torch.inference_mode():
            for _ in range(warmups):
                with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=amp):
                    model(benchmark_images)
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats(device)
            values: List[float] = []
            for _ in range(repetitions):
                _deadline_guard(started, int(config["budget"]["max_duration_seconds"]))
                begin = torch.cuda.Event(enable_timing=True)
                end = torch.cuda.Event(enable_timing=True)
                begin.record()
                with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=amp):
                    model(benchmark_images)
                end.record()
                torch.cuda.synchronize()
                values.append(float(begin.elapsed_time(end)))
            return values, int(torch.cuda.max_memory_allocated(device))

    baseline_latency, baseline_memory = measure(amp=False)
    candidate_latency, candidate_memory = measure(amp=True)
    telemetry.sample()
    _trace_event(events, "latency.raw.frozen", {"repetitions": repetitions})

    device_evidence = {
        "type": "cuda",
        "cuda_available": True,
        "visible_device_count": 1,
        "visible_device_binding": visible_ids[0],
        "name": str(torch.cuda.get_device_name(0)),
        "capability": list(torch.cuda.get_device_capability(0)),
        "cuda_runtime": str(torch.version.cuda),
        "torch_version": str(torch.__version__),
        "cudnn_version": int(torch.backends.cudnn.version() or 0),
    }
    metrics = {
        "trained_model_sha256": trained_model_sha256,
        "samples": samples,
        "latency_ms": {"baseline": baseline_latency, "candidate": candidate_latency},
        "max_memory_bytes": {"baseline": baseline_memory, "candidate": candidate_memory},
    }
    telemetry_records = telemetry.finish()
    _trace_event(events, "gpu.telemetry.frozen", {"samples": len(telemetry_records)})
    return device_evidence, metrics, events, model_state_bytes, telemetry_records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--physical-launch-id", required=True)
    parser.add_argument("--environment-lock-file", required=True)
    parser.add_argument("--approval-receipt-file", required=True)
    parser.add_argument("--agentteams-receipt-file", required=True)
    parser.add_argument("--matrix-plan-file", required=True)
    parser.add_argument(
        "--allow-download",
        action="store_true",
        help="Required in addition to dataset.download=true before any dataset download is allowed.",
    )
    return parser


def execute(arguments: argparse.Namespace) -> Dict[str, Any]:
    config_path = Path(arguments.config).resolve()
    config = load_and_validate_config(config_path)
    if bool(config["dataset"]["download"]) != bool(arguments.allow_download):
        raise ContractError(
            "config dataset.download and explicit --allow-download must agree"
        )
    binding_artifacts = {
        "environment_lock": _bound_file_record(
            str(arguments.environment_lock_file), "environment_lock_file", json_object=False
        ),
        "approval_receipt": _bound_file_record(
            str(arguments.approval_receipt_file), "approval_receipt_file", json_object=True
        ),
        "agentteams_receipt": _bound_file_record(
            str(arguments.agentteams_receipt_file), "agentteams_receipt_file", json_object=True
        ),
        "matrix_plan": _bound_file_record(
            str(arguments.matrix_plan_file), "matrix_plan_file", json_object=True
        ),
    }
    run_id = _validate_identifier(str(arguments.run_id), "run_id")
    physical_launch_id = _validate_identifier(
        str(arguments.physical_launch_id), "physical_launch_id"
    )
    git_commit = str(arguments.git_commit).lower()
    if len(git_commit) not in (40, 64) or any(
        character not in "0123456789abcdef" for character in git_commit
    ):
        raise ContractError("git_commit must be a 40- or 64-character hexadecimal object id")

    output_root = _prepare_output(Path(arguments.output_dir))
    data_root = Path(arguments.data_root).resolve()
    started = time.monotonic()
    device, metrics, events, model_state_bytes, telemetry_records = _run_torch_workload(
        config, data_root, started, physical_launch_id
    )
    duration_seconds = time.monotonic() - started
    dataset_manifest = _tree_manifest(data_root)
    if int(dataset_manifest["total_bytes"]) > int(config["budget"]["max_download_bytes"]):
        raise ContractError("dataset tree exceeds frozen byte budget")
    dataset_manifest_path = output_root / "dataset-manifest.json"
    dataset_manifest_path.write_bytes(canonical_bytes(dataset_manifest) + b"\n")
    dataset_manifest_sha256 = canonical_sha256(dataset_manifest)

    raw_without_digest: Dict[str, Any] = {
        "schema": RAW_SCHEMA,
        "execution_mode": "real_cuda",
        "synthetic": False,
        "physical_launch_count": 1,
        "run_id": run_id,
        "physical_launch_id": physical_launch_id,
        "cpu_fallback_used": False,
        "workload_id": config["workload_id"],
        "config": config,
        "config_sha256": canonical_sha256(config),
        "config_file_sha256": _sha256_file(config_path),
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "git_commit": git_commit,
        "git_commit_sha256": hashlib.sha256(git_commit.encode("ascii")).hexdigest(),
        "environment_lock_sha256": binding_artifacts["environment_lock"]["sha256"],
        "approval_receipt_sha256": binding_artifacts["approval_receipt"]["sha256"],
        "agentteams_receipt_sha256": binding_artifacts["agentteams_receipt"]["sha256"],
        "matrix_plan_sha256": binding_artifacts["matrix_plan"]["sha256"],
        "binding_artifacts": binding_artifacts,
        "device": device,
        "determinism": {
            "cublas_workspace_config": os.environ["CUBLAS_WORKSPACE_CONFIG"],
            "deterministic_algorithms": True,
            "cudnn_benchmark": False,
            "tf32": False,
        },
        "duration_seconds": duration_seconds,
        **metrics,
    }
    raw = add_raw_digest(raw_without_digest)
    decision = evaluate_raw_result(raw)
    _trace_event(
        events,
        "decision.recomputed",
        {"decision": decision["decision"], "raw_sha256": raw["raw_sha256"]},
    )

    raw_path = output_root / "raw-metrics.json"
    decision_path = output_root / "decision.json"
    trace_path = output_root / "trace.jsonl"
    model_path = output_root / "model-state.bin"
    telemetry_path = output_root / "gpu-telemetry.jsonl"
    matrix_metrics_path = output_root / "accuracy-matrix.jsonl"
    matrix_summary_path = output_root / "accuracy-summary.json"
    metric_contract_path = output_root / "metric-contract.json"
    raw_path.write_bytes(canonical_bytes(raw) + b"\n")
    decision_path.write_bytes(canonical_bytes(decision) + b"\n")
    trace_path.write_text(
        "".join(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n" for event in events),
        encoding="utf-8",
    )
    model_path.write_bytes(model_state_bytes)
    if _sha256_file(model_path) != raw["trained_model_sha256"]:
        raise ContractError("persisted model-state digest mismatch")
    telemetry_path.write_text(
        "".join(
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            for record in telemetry_records
        ),
        encoding="utf-8",
    )
    matrix_metric_bytes, matrix_summary, metric_contract = _acceptance_metric_artifacts(raw)
    matrix_metrics_path.write_bytes(matrix_metric_bytes)
    matrix_summary_path.write_bytes(canonical_bytes(matrix_summary) + b"\n")
    metric_contract_path.write_bytes(canonical_bytes(metric_contract) + b"\n")
    manifest = file_manifest(
        output_root,
        [
            dataset_manifest_path,
            raw_path,
            decision_path,
            trace_path,
            model_path,
            telemetry_path,
            matrix_metrics_path,
            matrix_summary_path,
            metric_contract_path,
        ],
    )
    manifest["artifact_root"] = canonical_sha256(manifest["files"])
    manifest_path = output_root / "artifact-manifest.json"
    manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")
    return {
        "ok": True,
        "decision": decision["decision"],
        "verification_status": decision["verification_status"],
        "live_claim_allowed": decision["live_claim_allowed"],
        "output_dir": str(output_root),
        "raw_sha256": raw["raw_sha256"],
        "artifact_root": manifest["artifact_root"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        result = execute(arguments)
    except ContractError as error:
        print(
            json.dumps(
                {"ok": False, "error": {"code": "contract_rejected", "message": str(error)}},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
