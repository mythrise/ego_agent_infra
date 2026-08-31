from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Type

from .canonical import canonical_bytes, canonical_sha256, parse_json_bytes, validate_sha256_digest
from .models import (
    CampaignEventCore,
    CandidateProposal,
    CheckpointCore,
    IssuedBudgetTicket,
    ModelRequest,
    ModelResponse,
    RunManifest,
    RunManifestCore,
    SignedTaskLease,
    StrictModel,
    TicketTemplate,
    TrustedFactCore,
    TrustedRelationCore,
)


PACKAGE_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_ROOT = PACKAGE_ROOT / "schemas"
DEFAULT_DIGEST_INDEX = REPOSITORY_ROOT / "docs/contracts/secure-agent/v2/contract-digests.json"

SCHEMA_MODELS: Mapping[str, Type[StrictModel]] = {
    "campaign-event-v1.schema.json": CampaignEventCore,
    "candidate-proposal-v1.schema.json": CandidateProposal,
    "checkpoint-v1.schema.json": CheckpointCore,
    "issued-budget-ticket-v1.schema.json": IssuedBudgetTicket,
    "model-request-v1.schema.json": ModelRequest,
    "model-response-v1.schema.json": ModelResponse,
    "run-manifest-v2.schema.json": RunManifest,
    "signed-task-lease-v1.schema.json": SignedTaskLease,
    "ticket-template-v1.schema.json": TicketTemplate,
    "trusted-fact-v1.schema.json": TrustedFactCore,
    "trusted-relation-v1.schema.json": TrustedRelationCore,
}


class SchemaContractError(ValueError):
    pass


def freeze_manifest(core: RunManifestCore) -> RunManifest:
    """Freeze an already-complete manifest core without adding live identifiers."""

    if type(core) is not RunManifestCore:
        raise TypeError("freeze_manifest requires a validated RunManifestCore")
    return RunManifest(core=core, manifest_sha256=canonical_sha256("run-manifest", core))


def _schema_bytes(model: Type[StrictModel]) -> bytes:
    schema = model.model_json_schema(mode="validation")
    return canonical_bytes(schema) + b"\n"


def _digest_index_bytes(digests: Mapping[str, str]) -> bytes:
    return canonical_bytes(
        {
            "schema_version": "secure-agent-contract-digests/v1",
            "schemas": dict(sorted(digests.items())),
        }
    ) + b"\n"


def export_schema_contract(
    *,
    schema_root: Path = DEFAULT_SCHEMA_ROOT,
    index_path: Path = DEFAULT_DIGEST_INDEX,
) -> None:
    schema_root.mkdir(parents=True, exist_ok=True)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    digests: Dict[str, str] = {}
    for filename, model in sorted(SCHEMA_MODELS.items()):
        payload = _schema_bytes(model)
        (schema_root / filename).write_bytes(payload)
        digests[filename] = hashlib.sha256(payload).hexdigest()
    index_path.write_bytes(_digest_index_bytes(digests))


def verify_schema_contract(
    *,
    schema_root: Path = DEFAULT_SCHEMA_ROOT,
    index_path: Path = DEFAULT_DIGEST_INDEX,
) -> None:
    expected_names = set(SCHEMA_MODELS)
    actual_names = {path.name for path in schema_root.glob("*.schema.json") if path.is_file()}
    missing_files = sorted(expected_names - actual_names)
    orphan_files = sorted(actual_names - expected_names)

    try:
        index = parse_json_bytes(index_path.read_bytes())
    except (OSError, TypeError, ValueError) as exc:
        raise SchemaContractError(f"invalid schema digest index: {exc}") from exc
    if not isinstance(index, dict) or set(index) != {"schema_version", "schemas"}:
        raise SchemaContractError("schema digest index has unknown or missing top-level fields")
    if index["schema_version"] != "secure-agent-contract-digests/v1":
        raise SchemaContractError("schema digest index has the wrong schema_version")
    indexed = index["schemas"]
    if not isinstance(indexed, dict):
        raise SchemaContractError("schema digest index schemas must be an object")
    indexed_names = set(indexed)
    missing_index = sorted(expected_names - indexed_names)
    extra_index = sorted(indexed_names - expected_names)

    problems = []
    if missing_files:
        problems.append("missing schema files: " + ", ".join(missing_files))
    if orphan_files:
        problems.append("orphan schema files: " + ", ".join(orphan_files))
    if missing_index:
        problems.append("missing schema digests: " + ", ".join(missing_index))
    if extra_index:
        problems.append("extra schema digests: " + ", ".join(extra_index))

    for filename, model in sorted(SCHEMA_MODELS.items()):
        path = schema_root / filename
        if not path.is_file() or filename not in indexed:
            continue
        payload = path.read_bytes()
        expected_payload = _schema_bytes(model)
        if payload != expected_payload:
            problems.append(f"changed schema: {filename}")
        recorded_digest = indexed[filename]
        try:
            validate_sha256_digest(recorded_digest)
        except (TypeError, ValueError):
            problems.append(f"invalid schema digest: {filename}")
            continue
        if recorded_digest != hashlib.sha256(payload).hexdigest():
            problems.append(f"changed schema digest: {filename}")

    canonical_index = _digest_index_bytes(
        {name: indexed[name] for name in sorted(indexed) if isinstance(indexed[name], str)}
    )
    if index_path.read_bytes() != canonical_index:
        problems.append("changed/non-canonical schema digest index")
    if problems:
        raise SchemaContractError("; ".join(problems))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Secure-memory manifest and schema tools")
    subparsers = parser.add_subparsers(dest="command", required=True)
    schema = subparsers.add_parser("schema", help="export or check canonical public schemas")
    schema.add_argument("--check", action="store_true", help="verify committed schemas and digests")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "schema":
        if args.check:
            verify_schema_contract()
        else:
            export_schema_contract()
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
