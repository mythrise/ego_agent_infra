from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Mapping, Type

from benchmarks.secure_memory.canonical import canonical_bytes, parse_json_bytes
from benchmarks.secure_memory.models import StrictModel

from .contracts import (
    AttentionPacket,
    CampaignBinding,
    GuardianDecision,
    SafetyDecision,
    UserStatusProjection,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCHEMA_ROOT = REPOSITORY_ROOT / "integrations/agentteams"

SCHEMA_MODELS: Mapping[str, Type[StrictModel]] = {
    "attention-packet.schema.json": AttentionPacket,
    "campaign-envelope.schema.json": CampaignBinding,
    "guardian-decision.schema.json": GuardianDecision,
    "safety-decision.schema.json": SafetyDecision,
    "user-status-projection.schema.json": UserStatusProjection,
}

# These are separate bridge transport contracts. They are deliberately not part
# of the D contract digest index, but their names are explicit so a new schema
# cannot enter this directory without review.
KNOWN_LEGACY_SCHEMA_NAMES = frozenset(
    {
        "message-envelope.schema.json",
        "result-envelope.schema.json",
    }
)


class SchemaContractError(ValueError):
    pass


class SemanticValidationError(ValueError):
    pass


def _mark_unique(properties: Mapping[str, Any], *fields: str) -> None:
    for field in fields:
        properties[field]["uniqueItems"] = True


def _mark_sorted_unique(properties: Mapping[str, Any], *fields: str) -> None:
    _mark_unique(properties, *fields)
    for field in fields:
        properties[field]["x-canonical-order"] = "ascending"


def _augment_nested_contracts(schema: Dict[str, Any]) -> None:
    definitions = schema.get("$defs", {})
    risk_assessment = definitions.get("RiskAssessment")
    if risk_assessment is not None:
        _mark_sorted_unique(
            risk_assessment["properties"],
            "reason_codes",
            "mandatory_constraint_ids",
        )

    canonical_effect = definitions.get("CanonicalEffect")
    if canonical_effect is not None:
        _mark_sorted_unique(canonical_effect["properties"], "affected_scope")

    approval_disclosure = definitions.get("ApprovalDisclosure")
    if approval_disclosure is not None:
        _mark_sorted_unique(
            approval_disclosure["properties"],
            "affected_scope",
            "reason_codes",
        )

    work_node = definitions.get("WorkNode")
    if work_node is not None:
        _mark_unique(work_node["properties"], "child_ids")

    work_hierarchy = definitions.get("WorkHierarchy")
    if work_hierarchy is not None:
        properties = work_hierarchy["properties"]
        _mark_unique(properties, "direct_child_ids", "nodes")
        properties["nodes"]["x-unique-by"] = "node_id"


def _augment_schema(filename: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    schema["$comment"] = (
        "Structural validation is necessary but not sufficient. Consumers MUST invoke the "
        "named canonical semantic validator before trusting or hashing a document."
    )
    schema["x-canonical-semantic-validator"] = (
        "apps.agentteams_bridge.extensions.schema_contract.validate_wire_document"
    )
    schema["x-semantic-validation-required"] = True
    _augment_nested_contracts(schema)

    properties = schema["properties"]
    if filename == "attention-packet.schema.json":
        _mark_sorted_unique(
            properties,
            "unresolved_failure_ids",
            "mandatory_policy_constraint_ids",
            "explicit_exclusions",
        )
        _mark_unique(properties, "eligible_fact_refs")
        properties["eligible_fact_refs"]["x-unique-by"] = "fact_sha256"
        properties["eligible_fact_refs"]["x-canonical-order"] = (
            "relevance_score_basis_points descending, fact_sha256 ascending"
        )
    elif filename == "user-status-projection.schema.json":
        _mark_unique(properties, "visible_node_ids", "override_node_ids", "explained_terms")
        _mark_sorted_unique(properties, "source_event_ids")
        properties["explained_terms"]["x-unique-by"] = "term"
        properties["explained_terms"]["x-canonical-order"] = "term ascending"
    return schema


def schema_bytes(filename: str) -> bytes:
    model = SCHEMA_MODELS.get(filename)
    if model is None:
        raise SchemaContractError(f"unknown AgentTeams schema: {filename}")
    schema = model.model_json_schema(mode="validation")
    return canonical_bytes(_augment_schema(filename, schema)) + b"\n"


def export_schema_contract(*, schema_root: Path = DEFAULT_SCHEMA_ROOT) -> Dict[str, str]:
    schema_root.mkdir(parents=True, exist_ok=True)
    digests: Dict[str, str] = {}
    for filename in sorted(SCHEMA_MODELS):
        payload = schema_bytes(filename)
        (schema_root / filename).write_bytes(payload)
        digests[filename] = hashlib.sha256(payload).hexdigest()
    return digests


def verify_schema_contract(*, schema_root: Path = DEFAULT_SCHEMA_ROOT) -> Dict[str, str]:
    expected_names = set(SCHEMA_MODELS)
    actual_names = {
        path.name for path in schema_root.glob("*.schema.json") if path.is_file()
    }
    missing_files = sorted(expected_names - actual_names)
    orphan_files = sorted(
        actual_names - expected_names - set(KNOWN_LEGACY_SCHEMA_NAMES)
    )
    problems = []
    if missing_files:
        problems.append("missing AgentTeams schema files: " + ", ".join(missing_files))
    if orphan_files:
        problems.append("orphan AgentTeams schema files: " + ", ".join(orphan_files))

    digests: Dict[str, str] = {}
    for filename in sorted(expected_names):
        path = schema_root / filename
        if not path.is_file():
            continue
        payload = path.read_bytes()
        if payload != schema_bytes(filename):
            problems.append(f"changed AgentTeams schema: {filename}")
        digests[filename] = hashlib.sha256(payload).hexdigest()

    if problems:
        raise SchemaContractError("; ".join(problems))
    return digests


def validate_wire_document(schema_filename: str, raw: bytes) -> StrictModel:
    """Parse and semantically validate bytes for one published AgentTeams schema."""

    model = SCHEMA_MODELS.get(schema_filename)
    if model is None:
        raise SemanticValidationError(f"unknown AgentTeams schema: {schema_filename}")
    try:
        return model.model_validate(parse_json_bytes(raw))
    except (TypeError, ValueError) as exc:
        raise SemanticValidationError(
            f"{schema_filename} failed canonical semantic validation: {exc}"
        ) from exc


__all__ = [
    "DEFAULT_SCHEMA_ROOT",
    "KNOWN_LEGACY_SCHEMA_NAMES",
    "SCHEMA_MODELS",
    "SchemaContractError",
    "SemanticValidationError",
    "export_schema_contract",
    "schema_bytes",
    "validate_wire_document",
    "verify_schema_contract",
]
