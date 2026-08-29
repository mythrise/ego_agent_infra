"""Build and verify deterministic, content-addressed semifinal proof bundles.

The source directory is an already captured local evidence tree.  This module never
contacts AgentTeams, Matrix, a scheduler, a GPU, or any other external service.
Adapters and collection tooling remain outside this trust boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, cast

from benchmarks.model import canonical_json, load_corpus
from benchmarks.trace_verifier import TraceValidationError, verify_trace_bytes
from protocols.rxp import RXPError, canonical_bytes, verify_ledger_document
from protocols.rxp.models import MatrixLedgerDocument


ACCEPTANCE_SCHEMA_VERSION = "egoagentos.semifinal-acceptance/v1"
INPUT_SCHEMA_VERSION = "egoagentos.semifinal-acceptance-input/v1"
FROZEN_INPUT_SCHEMA_VERSION = "egoagentos.semifinal-frozen-inputs/v1"
EVIDENCE_GATE_SCHEMA_VERSION = "egoagentos.semifinal-evidence-gate/v1"
DECISION_SCHEMA_VERSION = "egoagentos.semifinal-decision/v1"
REVIEW_SCHEMA_VERSION = "egoagentos.semifinal-review/v1"
RECOVERY_SCHEMA_VERSION = "egoagentos.semifinal-failure-recovery/v1"
AGENTTEAMS_RECEIPTS_SCHEMA_VERSION = "egoagentos.agentteams-receipts/v1"

MVP_SCENARIOS: Tuple[str, ...] = (
    "happy_path",
    "plan_conflict",
    "worker_timeout_reassign",
    "token_replay",
    "evidence_tamper",
    "forged_reviewer",
    "skill_version_rollback",
    "matrix_missing_seed",
)
EVIDENCE_KINDS: Tuple[str, ...] = (
    "code",
    "config",
    "dataset_manifest",
    "log",
    "metric",
    "trace",
    "review",
)
REQUIRED_FILE_KEYS: Tuple[str, ...] = (
    "frozen_inputs",
    "agentteams_receipts",
    "matrix_events",
    "gpu_raw_metrics",
    "raw_metrics",
    "metric_summary",
    "rxp_ledger",
    "evidence_gate",
    "failure_recovery",
    "checkpoint",
    "review",
    "decision",
)
BASE_RECEIPT_KINDS: Set[str] = {
    "project_create",
    "workflow_snapshot",
    "delegation",
    "ack",
    "submission",
    "acceptance",
    "spawn",
    "tool_result",
    "terminal",
}
RECOVERY_RECEIPT_KINDS: Set[str] = {
    "cancel",
    "replan",
}
BASE_MATRIX_EVENT_TYPES: Set[str] = {
    "TASK_REQUEST",
    "APPROVAL_GRANTED",
    "TERMINAL",
}
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
RAW_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SECRET_PATTERNS: Tuple[Tuple[str, re.Pattern[bytes]], ...] = (
    ("private key", re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("bearer token", re.compile(rb"(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    ("OpenAI-style key", re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("GitHub token", re.compile(rb"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("AWS access key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    (
        "secret JSON field",
        re.compile(
            rb'(?i)"(?:access_token|approval_token|auth_token|api_key|client_secret|private_key)"\s*:\s*"[^"\s]{4,}"'
        ),
    ),
)
SENSITIVE_FILENAMES = {".env", "id_rsa", "id_ed25519", "credentials.json"}


class AcceptanceError(ValueError):
    """The source or persisted bundle violates the acceptance contract."""


@dataclass(frozen=True)
class _SourceReport:
    descriptor: Dict[str, Any]
    trace_root: str
    matrix_events_root: str
    evidence_root: str
    benchmark_evidence_root: str
    rxp_ledger_root: str
    scenario_roots: Tuple[Dict[str, Any], ...]
    mvp_scenarios: Tuple[str, ...]


def _duplicate_free_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AcceptanceError("JSON contains duplicate key %r" % key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise AcceptanceError("JSON contains forbidden non-finite number %s" % value)


def _parse_json_bytes(payload: bytes, label: str) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_duplicate_free_object,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as error:
        raise AcceptanceError("%s is not UTF-8 JSON" % label) from error
    except json.JSONDecodeError as error:
        raise AcceptanceError("%s is invalid JSON: %s" % (label, str(error))) from error


def _load_json(path: Path, label: str) -> Any:
    if not path.is_file():
        raise AcceptanceError("%s is missing" % label)
    return _parse_json_bytes(path.read_bytes(), label)


def _load_jsonl(path: Path, label: str) -> List[Dict[str, Any]]:
    if not path.is_file():
        raise AcceptanceError("%s is missing" % label)
    records: List[Dict[str, Any]] = []
    for line_number, raw_line in enumerate(path.read_bytes().splitlines(), start=1):
        if not raw_line.strip():
            raise AcceptanceError("%s line %d is empty" % (label, line_number))
        value = _parse_json_bytes(raw_line, "%s line %d" % (label, line_number))
        records.append(_object(value, "%s line %d" % (label, line_number)))
    if not records:
        raise AcceptanceError("%s must contain at least one record" % label)
    return records


def _object(value: Any, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise AcceptanceError("%s must be an object" % label)
    return value


def _array(value: Any, label: str) -> List[Any]:
    if not isinstance(value, list):
        raise AcceptanceError("%s must be an array" % label)
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AcceptanceError("%s must be a non-empty trimmed string" % label)
    return value


def _integer(value: Any, label: str, minimum: Optional[int] = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise AcceptanceError("%s must be an integer" % label)
    if minimum is not None and value < minimum:
        raise AcceptanceError("%s must be >= %d" % (label, minimum))
    return value


def _sha256(value: Any, label: str) -> str:
    text = _text(value, label)
    if not SHA256_PATTERN.fullmatch(text):
        raise AcceptanceError("%s must be sha256:<64 lowercase hex>" % label)
    return text


def _raw_sha256(value: Any, label: str) -> str:
    text = _text(value, label)
    if not RAW_SHA256_PATTERN.fullmatch(text):
        raise AcceptanceError("%s must be 64 lowercase hex" % label)
    return text


def _digest_bytes(payload: bytes) -> str:
    return "sha256:%s" % hashlib.sha256(payload).hexdigest()


def _domain_digest(domain: str, value: Any) -> str:
    return _digest_bytes(domain.encode("utf-8") + b"\0" + canonical_bytes(value))


def decision_policy_digest(policy: Mapping[str, Any]) -> str:
    """Commit the deterministic verdict rule under a dedicated domain."""

    return _domain_digest("EgoAgentOS/semifinal-decision-policy/v1", policy)


def matrix_events_digest(records: Sequence[Mapping[str, Any]]) -> str:
    """Commit the ordered raw Matrix capture under a dedicated domain."""

    return _domain_digest("EgoAgentOS/matrix-events/v1", list(records))


def _fraction(value: Any, label: str, *, nonnegative: bool = False) -> Fraction:
    text = _text(value, label)
    try:
        result = Fraction(text)
    except (ValueError, ZeroDivisionError) as error:
        raise AcceptanceError("%s must be an exact rational" % label) from error
    if nonnegative and result < 0:
        raise AcceptanceError("%s must be non-negative" % label)
    return result


def _safe_relative(value: Any, label: str) -> Path:
    text = _text(value, label)
    relative = Path(text)
    if relative.is_absolute() or relative.parts in {(), (".",)} or ".." in relative.parts:
        raise AcceptanceError("%s must be a safe relative path" % label)
    if relative.as_posix() != text:
        raise AcceptanceError("%s must use normalized POSIX separators" % label)
    return relative


def _source_file(root: Path, value: Any, label: str) -> Path:
    relative = _safe_relative(value, label)
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise AcceptanceError("%s does not identify a regular source file" % label)
    return path


def _is_finite_number(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AcceptanceError("%s must be a finite JSON number" % label)
    if isinstance(value, float) and not math.isfinite(value):
        raise AcceptanceError("%s must be finite" % label)


def _contains_synthetic(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() == "synthetic" and item is True:
                return True
            if _contains_synthetic(item):
                return True
    elif isinstance(value, list):
        return any(_contains_synthetic(item) for item in value)
    elif isinstance(value, str):
        return value.upper() in {"SYNTHETIC", "SYNTHETIC_FIXTURE"}
    return False


def _scan_source_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise AcceptanceError("source contains a symlink: %s" % path.relative_to(root))
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if path.name in SENSITIVE_FILENAMES or path.suffix.lower() in {".pem", ".key", ".p12"}:
            raise AcceptanceError("source contains a sensitive filename: %s" % relative)
        payload = path.read_bytes()
        for name, pattern in SECRET_PATTERNS:
            if pattern.search(payload):
                raise AcceptanceError("source contains possible %s in %s" % (name, relative))
        if path.suffix.lower() == ".json":
            _parse_json_bytes(payload, relative.as_posix())
        elif path.suffix.lower() == ".jsonl":
            _load_jsonl(path, relative.as_posix())
        files.append(path)
    if not files:
        raise AcceptanceError("source directory is empty")
    return files


def _validate_descriptor(root: Path) -> Tuple[Dict[str, Any], Dict[str, Path]]:
    descriptor = _object(
        _load_json(root / "acceptance-input.json", "acceptance-input.json"),
        "acceptance-input.json",
    )
    if descriptor.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise AcceptanceError("unsupported acceptance input schema")
    _text(descriptor.get("acceptance_id"), "acceptance_id")
    _text(descriptor.get("created_at"), "created_at")
    if descriptor.get("gate_profile") != "mvp-8":
        raise AcceptanceError("gate_profile must be mvp-8")

    truth = _object(descriptor.get("truth_boundary"), "truth_boundary")
    if truth.get("execution_mode") != "real-agentteams":
        raise AcceptanceError("truth_boundary.execution_mode must be real-agentteams")
    if truth.get("synthetic") is not False:
        raise AcceptanceError("truth_boundary.synthetic must be false")
    if truth.get("gpu_execution") != "real":
        raise AcceptanceError("truth_boundary.gpu_execution must be real")
    if truth.get("external_origin_authentication") != "UNVERIFIED_OPERATOR_ASSERTION":
        raise AcceptanceError(
            "v1 accepts only UNVERIFIED_OPERATOR_ASSERTION; content hashes cannot authenticate origin"
        )

    run = _object(descriptor.get("run"), "run")
    if run.get("primary_scenario_id") != "worker_timeout_reassign":
        raise AcceptanceError("the single GPU proof must use worker_timeout_reassign")
    for key in ("task_id", "project_id", "correlation_id", "trace_id", "rxp_cell_id"):
        _text(run.get(key), "run.%s" % key)
    _integer(run.get("seed"), "run.seed", 0)

    corpus = load_corpus()
    corpus_input = _object(descriptor.get("corpus"), "corpus")
    expected = {
        "benchmark": "rxp-bench/v1",
        "corpus_version": corpus.corpus_version,
        "corpus_digest": corpus.digest,
        "total_scenarios": len(corpus.scenarios),
        "mvp_scenarios": list(MVP_SCENARIOS),
        "mvp_contract_status": "PASS",
        "full_release_status": "NOT_EVALUATED",
    }
    mismatched = [key for key, value in expected.items() if corpus_input.get(key) != value]
    if mismatched:
        raise AcceptanceError("corpus/gate declaration mismatch: %s" % ", ".join(mismatched))

    file_values = _object(descriptor.get("files"), "files")
    if set(file_values) != set(REQUIRED_FILE_KEYS):
        missing = sorted(set(REQUIRED_FILE_KEYS) - set(file_values))
        extra = sorted(set(file_values) - set(REQUIRED_FILE_KEYS))
        raise AcceptanceError("files map mismatch; missing=%s extra=%s" % (missing, extra))
    files = {
        key: _source_file(root, file_values[key], "files.%s" % key)
        for key in REQUIRED_FILE_KEYS
    }
    return descriptor, files


def _validate_frozen_inputs(path: Path) -> Dict[str, Any]:
    value = _object(_load_json(path, "frozen inputs"), "frozen inputs")
    if value.get("schema_version") != FROZEN_INPUT_SCHEMA_VERSION:
        raise AcceptanceError("unsupported frozen input schema")
    commit = _text(value.get("git_commit"), "frozen_inputs.git_commit")
    if not COMMIT_PATTERN.fullmatch(commit):
        raise AcceptanceError("frozen_inputs.git_commit must be 40 lowercase hex")
    if value.get("git_dirty") is not False:
        raise AcceptanceError("the accepted source commit must be clean")
    for key in (
        "container_image_digest",
        "config_digest",
        "environment_lock_digest",
        "dataset_manifest_digest",
        "model_digest",
        "skill_registry_digest",
        "agentteams_contract_digest",
    ):
        _sha256(value.get(key), "frozen_inputs.%s" % key)

    metric = _object(value.get("metric_contract"), "metric_contract")
    _text(metric.get("metric_name"), "metric_contract.metric_name")
    _integer(metric.get("scale"), "metric_contract.scale", 1)
    if metric.get("aggregation") != "mean":
        raise AcceptanceError("metric_contract.aggregation must be mean")
    samples = [_text(item, "metric_contract.sample_ids") for item in _array(metric.get("sample_ids"), "metric_contract.sample_ids")]
    if not samples or len(samples) != len(set(samples)):
        raise AcceptanceError("metric_contract.sample_ids must be non-empty and unique")
    cells = [_object(item, "metric_contract.matrix_cells") for item in _array(metric.get("matrix_cells"), "metric_contract.matrix_cells")]
    cell_ids = [_text(item.get("cell_id"), "matrix_cells.cell_id") for item in cells]
    for item in cells:
        _integer(item.get("seed"), "matrix_cells.seed", 0)
    if not cells or len(cell_ids) != len(set(cell_ids)):
        raise AcceptanceError("metric_contract.matrix_cells must be non-empty and unique")
    filters = [_text(item, "declared_filters") for item in _array(metric.get("declared_filters"), "declared_filters")]
    if len(filters) != len(set(filters)):
        raise AcceptanceError("declared_filters contains duplicates")

    policy = _object(metric.get("decision_policy"), "metric_contract.decision_policy")
    kind = policy.get("kind")
    for key in ("pass_rationale_code", "fail_rationale_code"):
        rationale = _text(policy.get(key), "decision_policy.%s" % key)
        if re.fullmatch(r"^[A-Z][A-Z0-9_]{1,63}$", rationale) is None:
            raise AcceptanceError("decision_policy.%s is not a rationale code" % key)
    if kind == "minimum_mean":
        if policy.get("cell_id") not in cell_ids:
            raise AcceptanceError("minimum_mean policy names an unknown cell")
        _fraction(policy.get("minimum_scaled_fraction"), "minimum_scaled_fraction")
    elif kind == "candidate_noninferiority":
        baseline = policy.get("baseline_cell_id")
        candidate = policy.get("candidate_cell_id")
        if baseline not in cell_ids or candidate not in cell_ids or baseline == candidate:
            raise AcceptanceError("candidate_noninferiority policy cell ids are invalid")
        _fraction(
            policy.get("max_degradation_scaled_fraction"),
            "max_degradation_scaled_fraction",
            nonnegative=True,
        )
    else:
        raise AcceptanceError("unsupported deterministic decision policy")

    budget = _object(value.get("budget"), "budget")
    if _integer(budget.get("max_gpu_count"), "budget.max_gpu_count", 1) != 1:
        raise AcceptanceError("the bounded semifinal profile requires exactly one GPU")
    _integer(budget.get("max_gpu_seconds"), "budget.max_gpu_seconds", 1)
    _integer(budget.get("max_wall_seconds"), "budget.max_wall_seconds", 1)
    _integer(budget.get("max_artifact_bytes"), "budget.max_artifact_bytes", 1)
    _integer(budget.get("max_retries"), "budget.max_retries", 0)
    _text(budget.get("currency"), "budget.currency")
    cost = _text(budget.get("max_cost_decimal"), "budget.max_cost_decimal")
    try:
        if Fraction(cost) < 0:
            raise ValueError
    except (ValueError, ZeroDivisionError) as error:
        raise AcceptanceError("budget.max_cost_decimal must be a non-negative rational") from error
    return value


def _validate_agentteams_receipts(
    root: Path,
    path: Path,
    traces: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    payload = _object(_load_json(path, "AgentTeams receipts"), "AgentTeams receipts")
    if payload.get("schema_version") != AGENTTEAMS_RECEIPTS_SCHEMA_VERSION:
        raise AcceptanceError("unsupported AgentTeams receipts schema")
    receipts = [_object(item, "AgentTeams receipt") for item in _array(payload.get("receipts"), "receipts")]
    if not receipts:
        raise AcceptanceError("AgentTeams receipts must be non-empty")
    by_id: Dict[str, Dict[str, Any]] = {}
    kinds_by_scenario: Dict[str, Set[str]] = {}
    raw_paths: Set[str] = set()
    raw_digests: Set[str] = set()
    request_ids: Set[str] = set()
    response_ids: Set[str] = set()
    for receipt in receipts:
        receipt_id = _text(receipt.get("receipt_id"), "receipt_id")
        if receipt_id in by_id:
            raise AcceptanceError("duplicate AgentTeams receipt_id %s" % receipt_id)
        kind = _text(receipt.get("kind"), "receipt.kind")
        scenario_id = _text(receipt.get("scenario_id"), "receipt.scenario_id")
        if scenario_id not in traces:
            raise AcceptanceError("AgentTeams receipt names a non-MVP scenario")
        trace = traces[scenario_id]
        if receipt.get("source") != "official-agentteams-api" or receipt.get("synthetic") is not False:
            raise AcceptanceError("every AgentTeams receipt must declare an official real source")
        if receipt.get("project_id") != trace["project_id"]:
            raise AcceptanceError("AgentTeams receipt project_id mismatch")
        if receipt.get("task_id") != trace["task_id"] or receipt.get("correlation_id") != trace["correlation_id"]:
            raise AcceptanceError("AgentTeams receipt task/correlation mismatch")
        method = _text(receipt.get("method"), "receipt.method")
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise AcceptanceError("receipt.method is not allowlisted")
        endpoint = _text(receipt.get("endpoint"), "receipt.endpoint")
        if not endpoint.startswith("/") or ".." in endpoint:
            raise AcceptanceError("receipt.endpoint must be an absolute API path")
        request_id = _text(receipt.get("request_id"), "receipt.request_id")
        response_id = _text(receipt.get("response_id"), "receipt.response_id")
        captured_at = _text(receipt.get("captured_at"), "receipt.captured_at")
        if request_id in request_ids or response_id in response_ids:
            raise AcceptanceError("AgentTeams request/response identifiers must be unique")
        request_ids.add(request_id)
        response_ids.add(response_id)
        status = _integer(receipt.get("status_code"), "receipt.status_code", 100)
        if status < 200 or status >= 300:
            raise AcceptanceError("official AgentTeams receipt is not a successful response")
        raw_file = _source_file(root, receipt.get("raw_file"), "receipt.raw_file")
        raw_relative = raw_file.relative_to(root).as_posix()
        raw_sha256 = _sha256(receipt.get("raw_sha256"), "receipt.raw_sha256")
        if raw_relative in raw_paths or raw_sha256 in raw_digests:
            raise AcceptanceError("each AgentTeams receipt must use unique raw response bytes")
        raw_paths.add(raw_relative)
        raw_digests.add(raw_sha256)
        raw_bytes = raw_file.read_bytes()
        if _digest_bytes(raw_bytes) != raw_sha256:
            raise AcceptanceError("AgentTeams raw response digest mismatch for %s" % receipt_id)
        raw = _object(_parse_json_bytes(raw_bytes, raw_relative), "raw AgentTeams response")
        expected_raw = {
            "schema_version": "egoagentos.agentteams-http-response/v1",
            "scenario_id": scenario_id,
            "kind": kind,
            "request_id": request_id,
            "response_id": response_id,
            "project_id": trace["project_id"],
            "task_id": trace["task_id"],
            "correlation_id": trace["correlation_id"],
            "method": method,
            "endpoint": endpoint,
            "status_code": status,
            "captured_at": captured_at,
        }
        mismatched_raw = [
            key for key, expected in expected_raw.items() if raw.get(key) != expected
        ]
        if mismatched_raw:
            raise AcceptanceError(
                "AgentTeams raw response/index mismatch for %s: %s"
                % (receipt_id, ", ".join(mismatched_raw))
            )
        body = _object(raw.get("body"), "raw AgentTeams response body")
        if body.get("ok") is not True:
            raise AcceptanceError("raw AgentTeams response body is not successful")
        by_id[receipt_id] = receipt
        kinds_by_scenario.setdefault(scenario_id, set()).add(kind)
    for scenario_id in MVP_SCENARIOS:
        required = set(BASE_RECEIPT_KINDS)
        if scenario_id == "worker_timeout_reassign":
            required.update(RECOVERY_RECEIPT_KINDS)
        elif scenario_id == "plan_conflict":
            required.add("replan")
        missing = sorted(required - kinds_by_scenario.get(scenario_id, set()))
        if missing:
            raise AcceptanceError(
                "AgentTeams receipts for %s lack required kinds: %s"
                % (scenario_id, missing)
            )
    return by_id


def _validate_matrix_events(
    path: Path, traces: Mapping[str, Mapping[str, Any]]
) -> Dict[str, Any]:
    records = _load_jsonl(path, "Matrix raw events")
    ids: Set[str] = set()
    kinds_by_scenario: Dict[str, Set[str]] = {}
    sequences_by_scenario: Dict[str, List[int]] = {}
    timestamps_by_scenario: Dict[str, List[int]] = {}
    rooms_by_scenario: Dict[str, Set[str]] = {}
    primary_recovery: Optional[Dict[str, Any]] = None
    for record in records:
        event_id = _text(record.get("event_id"), "Matrix event_id")
        if event_id in ids:
            raise AcceptanceError("duplicate Matrix event_id %s" % event_id)
        ids.add(event_id)
        event_type = _text(record.get("type"), "Matrix event type")
        scenario_id = _text(record.get("scenario_id"), "Matrix scenario_id")
        if scenario_id not in traces:
            raise AcceptanceError("Matrix event names a non-MVP scenario")
        trace = traces[scenario_id]
        kinds_by_scenario.setdefault(scenario_id, set()).add(event_type)
        sequences_by_scenario.setdefault(scenario_id, []).append(
            _integer(record.get("sequence"), "Matrix event sequence", 1)
        )
        if record.get("project_id") != trace["project_id"] or record.get("task_id") != trace["task_id"]:
            raise AcceptanceError("Matrix event project/task mismatch")
        if record.get("correlation_id") != trace["correlation_id"]:
            raise AcceptanceError("Matrix event correlation mismatch")
        room_id = _text(record.get("room_id"), "Matrix room_id")
        rooms_by_scenario.setdefault(scenario_id, set()).add(room_id)
        sender = _text(record.get("sender"), "Matrix sender")
        timestamp = _integer(record.get("origin_server_ts"), "Matrix origin_server_ts", 1)
        timestamps_by_scenario.setdefault(scenario_id, []).append(timestamp)
        content = _object(record.get("content"), "Matrix content")
        trace_rxp = _object(trace.get("rxp"), "trace RXP")
        common = {
            "event_type": event_type,
            "scenario_id": scenario_id,
            "task_id": trace["task_id"],
            "correlation_id": trace["correlation_id"],
            "matrix_root": trace_rxp["matrix_root"],
        }
        expected = dict(common)
        if event_type == "TASK_REQUEST":
            if not sender.startswith("@bridge"):
                raise AcceptanceError("TASK_REQUEST must be sent by the bridge principal")
            expected["intent_digest"] = trace_rxp["intent_digest"]
        elif event_type == "APPROVAL_GRANTED":
            if not sender.startswith("@human-"):
                raise AcceptanceError("APPROVAL_GRANTED must be sent by the human principal")
            approval_event_id = _object(
                trace.get("official_response_identifiers"),
                "official_response_identifiers",
            ).get("approval_matrix_event_id")
            expected.update(
                {
                    "grant_id": trace_rxp["grant_id"],
                    "receipt_digest": trace_rxp["receipt_digest"],
                    "approval_event_id": approval_event_id,
                }
            )
            if event_id != approval_event_id:
                raise AcceptanceError("Matrix approval event id does not match the trace")
        elif event_type == "FAILURE_RECOVERY":
            if not sender.startswith("@worker-"):
                raise AcceptanceError("FAILURE_RECOVERY must be sent by a worker principal")
            reassigned = [
                _object(item, "trace reassignment event")
                for item in _array(trace.get("events"), "trace events")
                if isinstance(item, dict) and item.get("type") == "task.reassigned"
            ]
            if len(reassigned) != 1:
                raise AcceptanceError("recovery Matrix event requires one trace reassignment")
            reassignment = _object(reassigned[0].get("payload"), "reassignment payload")
            expected.update(
                {
                    "old_worker_id": reassignment.get("from_assignee"),
                    "new_worker_id": reassignment.get("to_assignee"),
                    "effect_id": _text(content.get("effect_id"), "Matrix effect_id"),
                    "checkpoint_sha256": _sha256(
                        content.get("checkpoint_sha256"), "Matrix checkpoint_sha256"
                    ),
                }
            )
            if scenario_id == "worker_timeout_reassign":
                primary_recovery = dict(content)
        elif event_type == "TERMINAL":
            if not sender.startswith("@ego-"):
                raise AcceptanceError("TERMINAL must be sent by the Ego principal")
            decisions = [
                _object(item, "trace Decision event")
                for item in _array(trace.get("events"), "trace events")
                if isinstance(item, dict) and item.get("type") == "decision.committed"
            ]
            if len(decisions) != 1:
                raise AcceptanceError("terminal Matrix event requires one trace Decision")
            trace_decision = _object(decisions[0].get("payload"), "trace Decision payload")
            expected.update(
                {
                    "evidence_digest": trace_rxp["evidence_digest"],
                    "verdict": trace_decision.get("verdict"),
                }
            )
        else:
            raise AcceptanceError("unsupported Matrix event type %s" % event_type)
        if content != expected:
            raise AcceptanceError("Matrix content does not match its typed trace binding")
    for scenario_id in MVP_SCENARIOS:
        sequences = sequences_by_scenario.get(scenario_id, [])
        if sequences != list(range(1, len(sequences) + 1)):
            raise AcceptanceError(
                "Matrix event sequence for %s must be contiguous" % scenario_id
            )
        timestamps = timestamps_by_scenario.get(scenario_id, [])
        if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
            raise AcceptanceError("Matrix timestamps must be strictly increasing per scenario")
        if len(rooms_by_scenario.get(scenario_id, set())) != 1:
            raise AcceptanceError("each scenario must use exactly one Matrix room")
        required = set(BASE_MATRIX_EVENT_TYPES)
        if scenario_id == "worker_timeout_reassign":
            required.add("FAILURE_RECOVERY")
        missing = sorted(required - kinds_by_scenario.get(scenario_id, set()))
        if missing:
            raise AcceptanceError(
                "Matrix raw events for %s lack required types: %s"
                % (scenario_id, missing)
            )
    if primary_recovery is None:
        raise AcceptanceError("primary recovery Matrix event is missing")
    return {
        "root": matrix_events_digest(records),
        "primary_recovery": primary_recovery,
    }


def _validate_gpu_metrics(path: Path, frozen: Mapping[str, Any]) -> Dict[str, Any]:
    records = _load_jsonl(path, "GPU raw metrics")
    job_ids: Set[str] = set()
    gpu_ids: Set[str] = set()
    timestamps: List[int] = []
    for index, record in enumerate(records, start=1):
        if _integer(record.get("sequence"), "GPU sequence", 1) != index:
            raise AcceptanceError("GPU metric sequence must be contiguous")
        timestamp = _integer(record.get("timestamp_ns"), "GPU timestamp_ns", 0)
        timestamps.append(timestamp)
        job_ids.add(_text(record.get("job_id"), "GPU job_id"))
        gpu_ids.add(_text(record.get("gpu_uuid"), "GPU uuid"))
        _text(record.get("gpu_model"), "GPU model")
        utilization = record.get("utilization_pct")
        _is_finite_number(utilization, "GPU utilization_pct")
        if float(cast(Any, utilization)) < 0.0 or float(cast(Any, utilization)) > 100.0:
            raise AcceptanceError("GPU utilization_pct must be in [0, 100]")
        _integer(record.get("memory_used_bytes"), "GPU memory_used_bytes", 0)
        power = record.get("power_w")
        _is_finite_number(power, "GPU power_w")
        if float(cast(Any, power)) < 0.0:
            raise AcceptanceError("GPU power_w must be non-negative")
    if len(records) < 2 or len(job_ids) != 1 or len(gpu_ids) != 1:
        raise AcceptanceError("GPU evidence requires >=2 samples from one job and one GPU")
    if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
        raise AcceptanceError("GPU timestamps must be strictly increasing")
    gpu_seconds = (timestamps[-1] - timestamps[0]) // 1_000_000_000
    if gpu_seconds <= 0:
        raise AcceptanceError("GPU telemetry does not prove positive elapsed time")
    budget = _object(frozen.get("budget"), "budget")
    if gpu_seconds > int(budget["max_gpu_seconds"]):
        raise AcceptanceError("GPU telemetry exceeds the frozen GPU-time budget")
    return {"job_id": next(iter(job_ids)), "gpu_uuid": next(iter(gpu_ids)), "gpu_seconds": gpu_seconds}


def _validate_raw_metrics(
    raw_path: Path, summary_path: Path, frozen: Mapping[str, Any]
) -> Dict[str, Any]:
    metric = _object(frozen.get("metric_contract"), "metric_contract")
    metric_name = str(metric["metric_name"])
    sample_ids = [str(item) for item in metric["sample_ids"]]
    cell_ids = [str(_object(item, "matrix cell")["cell_id"]) for item in metric["matrix_cells"]]
    declared_filters = set(str(item) for item in metric["declared_filters"])
    expected_pairs = {(sample_id, cell_id) for sample_id in sample_ids for cell_id in cell_ids}
    records = _load_jsonl(raw_path, "raw metrics")
    seen_ids: Set[str] = set()
    seen_pairs: Set[Tuple[str, str]] = set()
    included_values: List[int] = []
    included_by_cell: Dict[str, List[int]] = {cell_id: [] for cell_id in cell_ids}
    filtered = 0
    for record in records:
        record_id = _text(record.get("record_id"), "raw metric record_id")
        if record_id in seen_ids:
            raise AcceptanceError("duplicate raw metric record_id %s" % record_id)
        seen_ids.add(record_id)
        sample_id = _text(record.get("sample_id"), "raw metric sample_id")
        cell_id = _text(record.get("cell_id"), "raw metric cell_id")
        pair = (sample_id, cell_id)
        if pair in seen_pairs:
            raise AcceptanceError("duplicate raw metric sample/cell pair %s" % (pair,))
        seen_pairs.add(pair)
        if record.get("metric_name") != metric_name:
            raise AcceptanceError("raw metric name does not match the frozen contract")
        value_scaled = _integer(record.get("value_scaled"), "raw metric value_scaled")
        included = record.get("included")
        if not isinstance(included, bool):
            raise AcceptanceError("raw metric included must be boolean")
        if included:
            if record.get("filter_id") not in (None, ""):
                raise AcceptanceError("included raw metric must not name a filter")
            included_values.append(value_scaled)
            included_by_cell[cell_id].append(value_scaled)
        else:
            filter_id = _text(record.get("filter_id"), "raw metric filter_id")
            if filter_id not in declared_filters:
                raise AcceptanceError("raw metric uses undeclared filter %s" % filter_id)
            filtered += 1
    missing = expected_pairs - seen_pairs
    extra = seen_pairs - expected_pairs
    if missing or extra:
        raise AcceptanceError("raw metric matrix mismatch; missing=%s extra=%s" % (sorted(missing), sorted(extra)))
    if not included_values:
        raise AcceptanceError("all raw metrics were filtered")
    total = sum(included_values)
    mean = Fraction(total, len(included_values))
    mean_text = "%d/%d" % (mean.numerator, mean.denominator)
    summary = _object(_load_json(summary_path, "metric summary"), "metric summary")
    expected_summary = {
        "metric_name": metric_name,
        "scale": metric["scale"],
        "aggregation": "mean",
        "included_n": len(included_values),
        "filtered_n": filtered,
        "sum_scaled": total,
        "mean_scaled_fraction": mean_text,
        "raw_metrics_sha256": _digest_bytes(raw_path.read_bytes()),
    }
    mismatched = [key for key, value in expected_summary.items() if summary.get(key) != value]
    if mismatched:
        raise AcceptanceError("metric summary mismatch: %s" % ", ".join(mismatched))

    policy = _object(metric.get("decision_policy"), "metric_contract.decision_policy")
    policy_kind = str(policy["kind"])
    if policy_kind == "minimum_mean":
        policy_cell = str(policy["cell_id"])
        values = included_by_cell[policy_cell]
        if not values:
            raise AcceptanceError("decision policy cell has no included values")
        passed = Fraction(sum(values), len(values)) >= _fraction(
            policy["minimum_scaled_fraction"], "minimum_scaled_fraction"
        )
    else:
        baseline_values = included_by_cell[str(policy["baseline_cell_id"])]
        candidate_values = included_by_cell[str(policy["candidate_cell_id"])]
        if not baseline_values or not candidate_values:
            raise AcceptanceError("decision policy cells have no included values")
        baseline_mean = Fraction(sum(baseline_values), len(baseline_values))
        candidate_mean = Fraction(sum(candidate_values), len(candidate_values))
        degradation_limit = _fraction(
            policy["max_degradation_scaled_fraction"],
            "max_degradation_scaled_fraction",
            nonnegative=True,
        )
        passed = candidate_mean >= baseline_mean - degradation_limit
    return {
        "summary": expected_summary,
        "verdict": "KEEP" if passed else "REJECT",
        "rationale_code": policy[
            "pass_rationale_code" if passed else "fail_rationale_code"
        ],
        "decision_policy_sha256": decision_policy_digest(policy),
    }


def _validate_rxp_ledger(path: Path, frozen: Mapping[str, Any], cell_id: str) -> Dict[str, Any]:
    raw = _load_json(path, "RXP ledger")
    if _contains_synthetic(raw):
        raise AcceptanceError("RXP ledger contains synthetic evidence")
    try:
        verify_ledger_document(raw)
        document = MatrixLedgerDocument.model_validate_json(canonical_bytes(raw))
    except (RXPError, ValueError) as error:
        raise AcceptanceError("RXP ledger verification failed: %s" % str(error)) from error
    if document.completeness != "COMPLETE" or document.missing_decisions:
        raise AcceptanceError("RXP MatrixLedger is incomplete")
    entries = [entry for entry in document.entries if entry.cell_id == cell_id]
    by_kind: Dict[str, List[Any]] = {}
    for entry in entries:
        by_kind.setdefault(entry.document_kind, []).append(entry)
    for kind in ("Intent", "Grant", "Receipt", "Decision"):
        if len(by_kind.get(kind, [])) != 1:
            raise AcceptanceError("RXP cell must contain exactly one %s" % kind)
    evidence_entries = by_kind.get("Evidence", [])
    evidence_types = [str(entry.document["evidence_type"]) for entry in evidence_entries]
    if sorted(evidence_types) != sorted(EVIDENCE_KINDS):
        raise AcceptanceError("RXP cell must contain exactly the seven required evidence kinds")

    intent_entry = by_kind["Intent"][0]
    grant_entry = by_kind["Grant"][0]
    receipt_entry = by_kind["Receipt"][0]
    decision_entry = by_kind["Decision"][0]
    manifest = _object(intent_entry.document.get("run_manifest"), "RXP run_manifest")
    comparisons = {
        "git_commit": frozen["git_commit"],
        "config_sha256": frozen["config_digest"],
        "dataset_manifest_sha256": frozen["dataset_manifest_digest"],
        "environment_lock_sha256": frozen["environment_lock_digest"],
        "base_model_sha256": frozen["model_digest"],
    }
    mismatch = [key for key, value in comparisons.items() if manifest.get(key) != value]
    if mismatch:
        raise AcceptanceError("RXP Intent does not bind frozen inputs: %s" % mismatch)
    receipt = _object(receipt_entry.document, "RXP Receipt")
    usage = _object(receipt.get("usage"), "RXP Receipt usage")
    metadata = _object(receipt.get("metadata"), "RXP Receipt metadata")
    job_id = _text(metadata.get("job_id"), "RXP Receipt job_id")
    budget = _object(frozen.get("budget"), "budget")
    if usage.get("gpu_count") != 1 or _integer(usage.get("gpu_time_seconds"), "RXP GPU seconds", 1) > budget["max_gpu_seconds"]:
        raise AcceptanceError("RXP Receipt does not prove one bounded real GPU execution")
    if _integer(usage.get("wall_time_seconds"), "RXP wall time", 1) > budget["max_wall_seconds"]:
        raise AcceptanceError("RXP Receipt exceeds wall-time budget")
    if _integer(usage.get("artifact_bytes"), "RXP artifact bytes", 1) > budget["max_artifact_bytes"]:
        raise AcceptanceError("RXP Receipt exceeds artifact budget")
    if receipt.get("determinism_level") != "D2_SEEDED_ENV_BOUND":
        raise AcceptanceError("a one-run GPU proof must declare D2_SEEDED_ENV_BOUND")

    evidence = {
        str(entry.document["evidence_type"]): {
            "document_digest": entry.document_digest,
            "producer_id": entry.document["producer_id"],
            "artifact_sha256": entry.document["artifact"]["sha256"],
        }
        for entry in evidence_entries
    }
    decision = _object(decision_entry.document, "RXP Decision")
    return {
        "ledger_root": document.root,
        "matrix_id": document.matrix_id,
        "intent_digest": intent_entry.document_digest,
        "grant_digest": grant_entry.document_digest,
        "grant_id": grant_entry.document["claims"]["grant_id"],
        "receipt_digest": receipt_entry.document_digest,
        "decision_digest": decision_entry.document_digest,
        "decision_document": decision,
        "evidence_root": decision["evidence_root"],
        "evidence": evidence,
        "usage": usage,
        "job_id": job_id,
    }


def _validate_scenarios(root: Path, descriptor: Mapping[str, Any]) -> Tuple[Tuple[Dict[str, Any], ...], Dict[str, Any]]:
    corpus = load_corpus()
    scenarios = {scenario.id: scenario for scenario in corpus.scenarios}
    results = [_object(item, "scenario result") for item in _array(descriptor.get("scenario_results"), "scenario_results")]
    if len(results) != len(scenarios):
        raise AcceptanceError("scenario_results must declare all 14 corpus scenarios")
    by_id: Dict[str, Dict[str, Any]] = {}
    verified_roots: List[Dict[str, Any]] = []
    verified_trace_values: Dict[str, Dict[str, Any]] = {}
    for result in results:
        scenario_id = _text(result.get("scenario_id"), "scenario_id")
        if scenario_id not in scenarios or scenario_id in by_id:
            raise AcceptanceError("unknown or duplicate scenario result %s" % scenario_id)
        by_id[scenario_id] = result
        expected_status = "PASS" if scenario_id in MVP_SCENARIOS else "SKIP"
        if result.get("status") != expected_status:
            raise AcceptanceError("MVP requires %s=%s" % (scenario_id, expected_status))
        if expected_status == "SKIP":
            _text(result.get("reason"), "%s skip reason" % scenario_id)
            if "trace_file" in result:
                raise AcceptanceError("SKIP scenario must not carry a trace_file")
            continue
        seed = _integer(result.get("seed"), "%s seed" % scenario_id, 0)
        repetition = _integer(result.get("repetition"), "%s repetition" % scenario_id, 0)
        trace_path = _source_file(root, result.get("trace_file"), "%s trace_file" % scenario_id)
        payload = trace_path.read_bytes()
        try:
            verified = verify_trace_bytes(payload, scenario=scenarios[scenario_id], seed=seed)
        except TraceValidationError as error:
            raise AcceptanceError("%s trace failed: %s" % (scenario_id, str(error))) from error
        trace_value = _object(_parse_json_bytes(payload, "%s trace" % scenario_id), "trace")
        trace_boundary = _text(trace_value.get("truth_boundary"), "trace.truth_boundary")
        if (
            trace_value.get("execution_mode") != "real-agentteams"
            or _contains_synthetic(trace_value)
            or "synthetic" in trace_boundary.lower()
            or "fixture" in trace_boundary.lower()
        ):
            raise AcceptanceError("%s trace crosses the real/synthetic truth boundary" % scenario_id)
        verified_trace_values[scenario_id] = trace_value
        verified_roots.append(
            {
                "scenario_id": scenario_id,
                "repetition": repetition,
                "seed": seed,
                "trace_file": str(result["trace_file"]),
                "trace_root": verified.trace_root,
                "benchmark_evidence_root": verified.evidence_root,
            }
        )
    if set(by_id) != set(scenarios):
        raise AcceptanceError("scenario_results do not equal the canonical corpus")
    primary_id = str(_object(descriptor.get("run"), "run")["primary_scenario_id"])
    primary = next(item for item in verified_roots if item["scenario_id"] == primary_id)
    primary_value = verified_trace_values[primary_id]
    return tuple(sorted(verified_roots, key=lambda item: item["scenario_id"])), {
        "roots": primary,
        "trace": primary_value,
        "traces": verified_trace_values,
    }


def _validate_evidence_gate(
    root: Path, path: Path, rxp: Mapping[str, Any], review_path: Path
) -> str:
    gate = _object(_load_json(path, "Evidence Gate"), "Evidence Gate")
    if gate.get("schema_version") != EVIDENCE_GATE_SCHEMA_VERSION or gate.get("status") != "PASS":
        raise AcceptanceError("Evidence Gate must be a v1 PASS")
    if gate.get("required_kinds") != list(EVIDENCE_KINDS):
        raise AcceptanceError("Evidence Gate required_kinds mismatch")
    evidence_items = [_object(item, "Evidence Gate item") for item in _array(gate.get("evidence"), "Evidence Gate evidence")]
    if len(evidence_items) != len(EVIDENCE_KINDS):
        raise AcceptanceError("Evidence Gate must contain seven evidence records")
    seen: Set[str] = set()
    producers: Dict[str, str] = {}
    for item in evidence_items:
        kind = _text(item.get("kind"), "evidence kind")
        if kind not in EVIDENCE_KINDS or kind in seen:
            raise AcceptanceError("missing or duplicate Evidence Gate kind %s" % kind)
        seen.add(kind)
        expected = _object(rxp["evidence"], "RXP evidence")[kind]
        if item.get("rxp_document_digest") != expected["document_digest"]:
            raise AcceptanceError("Evidence Gate RXP document digest mismatch for %s" % kind)
        if item.get("producer_id") != expected["producer_id"]:
            raise AcceptanceError("Evidence Gate producer mismatch for %s" % kind)
        artifact = _source_file(root, item.get("artifact_file"), "evidence artifact_file")
        artifact_digest = _digest_bytes(artifact.read_bytes())
        if artifact_digest != _sha256(item.get("artifact_sha256"), "evidence artifact_sha256"):
            raise AcceptanceError("Evidence Gate artifact digest mismatch for %s" % kind)
        if artifact_digest != expected["artifact_sha256"]:
            raise AcceptanceError("Evidence artifact is not bound into the RXP document for %s" % kind)
        producers[kind] = str(item["producer_id"])
    evidence_root = _sha256(gate.get("evidence_root"), "Evidence Gate evidence_root")
    if evidence_root != rxp["evidence_root"]:
        raise AcceptanceError("Evidence Gate root does not match the RXP Decision")
    reviewer = _text(gate.get("independent_reviewer_id"), "independent_reviewer_id")
    if producers.get("review") != reviewer or reviewer in {
        producer for kind, producer in producers.items() if kind != "review"
    }:
        raise AcceptanceError("Evidence Gate reviewer is not independent")
    if review_path != _source_file(root, next(item["artifact_file"] for item in evidence_items if item["kind"] == "review"), "review artifact"):
        raise AcceptanceError("descriptor review file differs from Evidence Gate review artifact")
    return evidence_root


def _validate_review(path: Path, evidence_root: str, rxp: Mapping[str, Any]) -> None:
    review = _object(_load_json(path, "independent review"), "independent review")
    if review.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise AcceptanceError("unsupported review schema")
    if review.get("independent") is not True or review.get("verdict") != "PASS":
        raise AcceptanceError("independent review must PASS")
    reviewer = _text(review.get("reviewer_id"), "reviewer_id")
    if reviewer != rxp["evidence"]["review"]["producer_id"]:
        raise AcceptanceError("reviewer identity differs from RXP Evidence")
    covered = [_text(item, "reviewed producer") for item in _array(review.get("reviewed_producers"), "reviewed_producers")]
    expected = sorted(
        {
            item["producer_id"]
            for kind, item in rxp["evidence"].items()
            if kind != "review"
        }
    )
    if sorted(covered) != expected or len(covered) != len(set(covered)):
        raise AcceptanceError("review does not cover every non-review producer")
    reviewed_trace = _sha256(
        review.get("reviewed_trace_sha256"), "review.reviewed_trace_sha256"
    )
    if reviewed_trace != rxp["evidence"]["trace"]["artifact_sha256"]:
        raise AcceptanceError("review does not bind the RXP trace Evidence artifact")
    if review.get("evidence_root_after_review") not in (None, evidence_root):
        raise AcceptanceError("review names a mismatched evidence root")


def _validate_recovery(
    root: Path,
    path: Path,
    checkpoint_path: Path,
    receipts: Mapping[str, Any],
    primary_trace: Mapping[str, Any],
    matrix_recovery: Mapping[str, Any],
) -> None:
    recovery = _object(_load_json(path, "failure recovery"), "failure recovery")
    if recovery.get("schema_version") != RECOVERY_SCHEMA_VERSION:
        raise AcceptanceError("unsupported failure recovery schema")
    if recovery.get("scenario_id") != "worker_timeout_reassign" or recovery.get("fault_type") != "worker_timeout":
        raise AcceptanceError("failure recovery must prove worker_timeout_reassign")
    checkpoint_file = _source_file(
        root, recovery.get("checkpoint_file"), "recovery.checkpoint_file"
    )
    if checkpoint_file != checkpoint_path:
        raise AcceptanceError("recovery checkpoint differs from the descriptor")
    checkpoint_sha256 = _sha256(recovery.get("checkpoint_sha256"), "checkpoint_sha256")
    if _digest_bytes(checkpoint_file.read_bytes()) != checkpoint_sha256:
        raise AcceptanceError("recovery checkpoint artifact digest mismatch")
    old_worker = _text(recovery.get("old_worker_id"), "old_worker_id")
    new_worker = _text(recovery.get("new_worker_id"), "new_worker_id")
    if old_worker == new_worker:
        raise AcceptanceError("failure recovery did not change workers")
    if recovery.get("old_worker_fenced") is not True or recovery.get("checkpoint_restored") is not True:
        raise AcceptanceError("failure recovery lacks fencing/checkpoint restoration")
    effect_ids = [_text(item, "effect_id") for item in _array(recovery.get("effect_ids"), "effect_ids")]
    if len(effect_ids) != 1:
        raise AcceptanceError("failure recovery must prove exactly one effect")
    idempotency_key = _text(recovery.get("idempotency_key"), "idempotency_key")
    started_ns = _integer(recovery.get("recovery_started_at_ns"), "recovery_started_at_ns", 0)
    completed_ns = _integer(
        recovery.get("recovery_completed_at_ns"), "recovery_completed_at_ns", 0
    )
    if completed_ns <= started_ns:
        raise AcceptanceError("recovery timestamps are not strictly increasing")
    mttr_ms = _integer(recovery.get("mttr_ms"), "mttr_ms", 0)
    if mttr_ms != (completed_ns - started_ns) // 1_000_000:
        raise AcceptanceError("recovery MTTR is not recomputable from timestamps")
    receipt_ids = [_text(item, "recovery receipt_id") for item in _array(recovery.get("official_receipt_ids"), "official_receipt_ids")]
    if not receipt_ids or any(item not in receipts for item in receipt_ids):
        raise AcceptanceError("failure recovery references missing official receipts")
    kinds = {receipts[item]["kind"] for item in receipt_ids}
    if not {"cancel", "replan"}.issubset(kinds):
        raise AcceptanceError("failure recovery must reference official cancel and replan receipts")
    fence_receipt_id = _text(
        recovery.get("scheduler_fence_receipt_id"), "scheduler_fence_receipt_id"
    )
    if fence_receipt_id not in receipts or receipts[fence_receipt_id]["kind"] != "cancel":
        raise AcceptanceError("scheduler fencing is not bound to the cancel receipt")

    expected_matrix = {
        "old_worker_id": old_worker,
        "new_worker_id": new_worker,
        "effect_id": effect_ids[0],
        "checkpoint_sha256": checkpoint_sha256,
    }
    if any(matrix_recovery.get(key) != value for key, value in expected_matrix.items()):
        raise AcceptanceError("failure recovery disagrees with the Matrix recovery event")
    trace_events = [
        _object(item, "recovery trace event")
        for item in _array(primary_trace.get("events"), "primary trace events")
    ]
    reassignments = [item for item in trace_events if item.get("type") == "task.reassigned"]
    effects = [item for item in trace_events if item.get("type") == "effect.committed"]
    if len(reassignments) != 1 or len(effects) != 1:
        raise AcceptanceError("recovery trace must contain one reassignment and one effect")
    reassignment = _object(reassignments[0].get("payload"), "reassignment payload")
    effect = _object(effects[0].get("payload"), "effect payload")
    if (
        reassignment.get("from_assignee") != old_worker
        or reassignment.get("to_assignee") != new_worker
        or effect.get("effect_id") != effect_ids[0]
        or effect.get("idempotency_key") != idempotency_key
        or int(reassignments[0]["sequence"]) >= int(effects[0]["sequence"])
    ):
        raise AcceptanceError("failure recovery disagrees with the trace effect chain")


def _validate_decision(
    path: Path,
    trace_root: str,
    evidence_root: str,
    rxp: Mapping[str, Any],
    primary_trace: Mapping[str, Any],
    metric_result: Mapping[str, Any],
    matrix_events_root: str,
) -> None:
    decision = _object(_load_json(path, "acceptance decision"), "acceptance decision")
    if decision.get("schema_version") != DECISION_SCHEMA_VERSION:
        raise AcceptanceError("unsupported decision schema")
    if decision.get("gate_status") != "PASS" or decision.get("verdict") not in {"KEEP", "REJECT"}:
        raise AcceptanceError("Decision requires a passing gate and explicit verdict")
    _text(decision.get("decided_by"), "decision.decided_by")
    if decision.get("trace_root") != trace_root or decision.get("evidence_root") != evidence_root:
        raise AcceptanceError("Decision does not bind the verified trace/evidence roots")
    if decision.get("rxp_decision_digest") != rxp["decision_digest"]:
        raise AcceptanceError("Decision does not bind the RXP Decision")
    expected_verdict = metric_result["verdict"]
    expected_rationale = metric_result["rationale_code"]
    expected_policy_digest = metric_result["decision_policy_sha256"]
    if (
        decision.get("verdict") != expected_verdict
        or decision.get("rationale_code") != expected_rationale
        or decision.get("decision_policy_sha256") != expected_policy_digest
        or decision.get("matrix_events_root") != matrix_events_root
    ):
        raise AcceptanceError("top-level Decision disagrees with recomputed raw metrics")

    rxp_decision = _object(rxp.get("decision_document"), "RXP Decision")
    if (
        rxp_decision.get("verdict") != expected_verdict
        or rxp_decision.get("rationale_code") != expected_rationale
    ):
        raise AcceptanceError("RXP Decision disagrees with recomputed raw metrics")

    trace_events = [
        _object(item, "primary trace event")
        for item in _array(primary_trace.get("events"), "primary trace events")
        if isinstance(item, dict) and item.get("type") == "decision.committed"
    ]
    if len(trace_events) != 1:
        raise AcceptanceError("primary trace must contain exactly one committed Decision")
    trace_decision = _object(trace_events[0].get("payload"), "trace Decision payload")
    if (
        trace_decision.get("verdict") != expected_verdict
        or trace_decision.get("rationale_code") != expected_rationale
        or trace_decision.get("decision_policy_sha256") != expected_policy_digest
        or trace_decision.get("matrix_events_root") != matrix_events_root
    ):
        raise AcceptanceError("trace Decision disagrees with recomputed raw metrics")


def _validate_source(root: Path) -> _SourceReport:
    root = root.resolve()
    if not root.is_dir():
        raise AcceptanceError("source must be a directory")
    _scan_source_files(root)
    descriptor, files = _validate_descriptor(root)
    run = _object(descriptor["run"], "run")
    frozen = _validate_frozen_inputs(files["frozen_inputs"])
    scenario_roots, primary = _validate_scenarios(root, descriptor)
    primary_trace = _object(primary["trace"], "primary trace")
    for key in ("task_id", "project_id", "correlation_id", "trace_id", "seed"):
        if primary_trace.get(key) != run.get(key):
            raise AcceptanceError("primary trace does not match run.%s" % key)
    verified_traces = _object(primary["traces"], "verified traces")
    receipts = _validate_agentteams_receipts(
        root, files["agentteams_receipts"], verified_traces
    )
    matrix_report = _validate_matrix_events(files["matrix_events"], verified_traces)
    matrix_events_root = str(matrix_report["root"])
    gpu = _validate_gpu_metrics(files["gpu_raw_metrics"], frozen)
    metric_result = _validate_raw_metrics(
        files["raw_metrics"], files["metric_summary"], frozen
    )
    rxp = _validate_rxp_ledger(files["rxp_ledger"], frozen, str(run["rxp_cell_id"]))
    trace_rxp = _object(primary_trace.get("rxp"), "primary trace RXP")
    expected_trace_rxp = {
        "intent_digest": rxp["intent_digest"],
        "grant_id": rxp["grant_id"],
        "receipt_digest": rxp["receipt_digest"],
        "evidence_digest": rxp["evidence_root"],
        "matrix_root": rxp["ledger_root"],
    }
    mismatch = [key for key, value in expected_trace_rxp.items() if trace_rxp.get(key) != value]
    if mismatch:
        raise AcceptanceError("primary trace does not bind the RXP chain: %s" % mismatch)
    reported_gpu_seconds = int(rxp["usage"]["gpu_time_seconds"])
    observed_gpu_seconds = int(gpu["gpu_seconds"])
    tolerance_seconds = max(5, math.ceil(observed_gpu_seconds * 0.05))
    if abs(reported_gpu_seconds - observed_gpu_seconds) > tolerance_seconds:
        raise AcceptanceError("RXP GPU usage does not match captured telemetry time")
    if rxp["job_id"] != gpu["job_id"]:
        raise AcceptanceError("RXP Receipt job_id does not match GPU telemetry")
    evidence_root = _validate_evidence_gate(root, files["evidence_gate"], rxp, files["review"])
    trace_root = str(primary["roots"]["trace_root"])
    _validate_review(files["review"], evidence_root, rxp)
    _validate_recovery(
        root,
        files["failure_recovery"],
        files["checkpoint"],
        receipts,
        primary_trace,
        _object(matrix_report["primary_recovery"], "primary Matrix recovery"),
    )
    _validate_decision(
        files["decision"],
        trace_root,
        evidence_root,
        rxp,
        primary_trace,
        metric_result,
        matrix_events_root,
    )
    return _SourceReport(
        descriptor=descriptor,
        trace_root=trace_root,
        matrix_events_root=matrix_events_root,
        evidence_root=evidence_root,
        benchmark_evidence_root=str(primary["roots"]["benchmark_evidence_root"]),
        rxp_ledger_root=str(rxp["ledger_root"]),
        scenario_roots=scenario_roots,
        mvp_scenarios=MVP_SCENARIOS,
    )


def _file_records(root: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in _scan_source_files(root):
        payload = path.read_bytes()
        records.append(
            {
                "path": "artifacts/%s" % path.relative_to(root).as_posix(),
                "sha256": _digest_bytes(payload),
                "bytes": len(payload),
            }
        )
    return records


def _bundle_commitment(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": manifest["schema_version"],
        "acceptance_id": manifest["acceptance_id"],
        "created_at": manifest["created_at"],
        "truth_boundary": manifest["truth_boundary"],
        "gate": manifest["gate"],
        "roots": {
            "trace_root": manifest["roots"]["trace_root"],
            "matrix_events_root": manifest["roots"]["matrix_events_root"],
            "evidence_root": manifest["roots"]["evidence_root"],
            "benchmark_evidence_root": manifest["roots"]["benchmark_evidence_root"],
            "rxp_ledger_root": manifest["roots"]["rxp_ledger_root"],
        },
        "scenarios": manifest["scenarios"],
        "files": manifest["files"],
    }


def _manifest_for_source(root: Path, report: _SourceReport) -> Dict[str, Any]:
    descriptor = report.descriptor
    manifest: Dict[str, Any] = {
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "acceptance_id": descriptor["acceptance_id"],
        "created_at": descriptor["created_at"],
        "truth_boundary": {
            **_object(descriptor["truth_boundary"], "truth_boundary"),
            "source_authenticity_status": "UNVERIFIED",
            "live_claim_allowed": False,
            "content_hash_limit": (
                "The bundle proves byte integrity and cross-artifact consistency; content "
                "hashes alone are not a third-party signature of an external service."
            ),
        },
        "gate": {
            "profile": "mvp-8-contract",
            "mvp_contract_status": "PASS",
            "source_authenticity_status": "UNVERIFIED",
            "live_claim_status": "NOT_VERIFIED",
            "passed_scenarios": list(report.mvp_scenarios),
            "passed_count": 8,
            "corpus_count": 14,
            "coverage_fraction": "8/14",
            "full_release_status": "NOT_EVALUATED",
            "full_release_reason": (
                "The full gate requires all 14 scenarios at the configured repetitions; "
                "an MVP bundle can never promote itself to full PASS."
            ),
        },
        "roots": {
            "trace_root": report.trace_root,
            "matrix_events_root": report.matrix_events_root,
            "evidence_root": report.evidence_root,
            "benchmark_evidence_root": report.benchmark_evidence_root,
            "rxp_ledger_root": report.rxp_ledger_root,
            "bundle_root": "",
        },
        "scenarios": list(report.scenario_roots),
        "files": _file_records(root),
    }
    manifest["roots"]["bundle_root"] = _domain_digest(
        "EgoAgentOS/semifinal-acceptance-bundle/v1",
        _bundle_commitment(manifest),
    )
    return manifest


def build_bundle(source: Path, output: Path) -> Dict[str, Any]:
    """Validate a local capture and materialize a deterministic immutable bundle."""

    source = source.resolve()
    output = output.resolve()
    if source == output or source in output.parents or output in source.parents:
        raise AcceptanceError("source and output must be disjoint directories")
    if output.exists() and any(output.iterdir() if output.is_dir() else [output]):
        raise AcceptanceError("output must not exist or must be empty")
    report = _validate_source(source)
    manifest = _manifest_for_source(source, report)
    output.mkdir(parents=True, exist_ok=True)
    artifact_root = output / "artifacts"
    for source_path in _scan_source_files(source):
        destination = artifact_root / source_path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination)
    manifest_bytes = (canonical_json(manifest) + "\n").encode("utf-8")
    (output / "manifest.json").write_bytes(manifest_bytes)
    (output / "manifest.sha256").write_text(
        hashlib.sha256(manifest_bytes).hexdigest() + "  manifest.json\n",
        encoding="utf-8",
    )
    return {
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "status": "CONTRACT_PASS_ORIGIN_UNVERIFIED",
        "contract_status": "PASS",
        "external_origin_status": "UNVERIFIED",
        "live_claim_allowed": False,
        "bundle": str(output),
        "bundle_root": manifest["roots"]["bundle_root"],
        "mvp_coverage": "8/14",
        "full_release_status": "NOT_EVALUATED",
    }


def verify_bundle(bundle: Path) -> Dict[str, Any]:
    """Verify a persisted bundle entirely from its local bytes."""

    bundle = bundle.resolve()
    if not bundle.is_dir():
        raise AcceptanceError("bundle must be a directory")
    manifest_path = bundle / "manifest.json"
    manifest_bytes = manifest_path.read_bytes() if manifest_path.is_file() else b""
    manifest = _object(_parse_json_bytes(manifest_bytes, "manifest.json"), "manifest")
    if manifest.get("schema_version") != ACCEPTANCE_SCHEMA_VERSION:
        raise AcceptanceError("unsupported acceptance manifest schema")
    checksum_path = bundle / "manifest.sha256"
    expected_checksum = hashlib.sha256(manifest_bytes).hexdigest() + "  manifest.json\n"
    if not checksum_path.is_file() or checksum_path.read_text(encoding="utf-8") != expected_checksum:
        raise AcceptanceError("manifest.sha256 mismatch")
    files = [_object(item, "manifest file") for item in _array(manifest.get("files"), "manifest files")]
    paths: Set[str] = set()
    for item in files:
        relative = _safe_relative(item.get("path"), "manifest file path")
        if relative.parts[0] != "artifacts" or relative.as_posix() in paths:
            raise AcceptanceError("duplicate or non-artifact manifest path")
        paths.add(relative.as_posix())
        path = bundle / relative
        if path.is_symlink() or not path.is_file():
            raise AcceptanceError("manifest artifact is missing: %s" % relative)
        payload = path.read_bytes()
        if _digest_bytes(payload) != _sha256(item.get("sha256"), "manifest file sha256"):
            raise AcceptanceError("artifact digest mismatch: %s" % relative)
        if len(payload) != _integer(item.get("bytes"), "manifest file bytes", 0):
            raise AcceptanceError("artifact size mismatch: %s" % relative)
    actual_paths = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file() and path not in {manifest_path, checksum_path}
    }
    if actual_paths != paths:
        raise AcceptanceError("bundle contains missing or undeclared artifacts")
    artifact_root = bundle / "artifacts"
    source_report = _validate_source(artifact_root)
    rebuilt = _manifest_for_source(artifact_root, source_report)
    if rebuilt != manifest:
        raise AcceptanceError("manifest does not match independently replayed evidence")
    claimed_root = _sha256(_object(manifest.get("roots"), "roots").get("bundle_root"), "bundle_root")
    recomputed = _domain_digest(
        "EgoAgentOS/semifinal-acceptance-bundle/v1",
        _bundle_commitment(manifest),
    )
    if claimed_root != recomputed:
        raise AcceptanceError("bundle_root mismatch")
    gate = _object(manifest.get("gate"), "gate")
    if (
        gate.get("mvp_contract_status") != "PASS"
        or gate.get("source_authenticity_status") != "UNVERIFIED"
        or gate.get("live_claim_status") != "NOT_VERIFIED"
        or gate.get("coverage_fraction") != "8/14"
    ):
        raise AcceptanceError("bundle does not preserve the contract/origin truth boundary")
    if gate.get("full_release_status") != "NOT_EVALUATED":
        raise AcceptanceError("MVP bundle illegally promotes the full release gate")
    return {
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "status": "CONTRACT_PASS_ORIGIN_UNVERIFIED",
        "contract_status": "PASS",
        "external_origin_status": "UNVERIFIED",
        "live_claim_allowed": False,
        "bundle": str(bundle),
        "bundle_root": claimed_root,
        "mvp_coverage": "8/14",
        "full_release_status": "NOT_EVALUATED",
        "external_calls": 0,
    }
