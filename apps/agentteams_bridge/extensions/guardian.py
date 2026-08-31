from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, Mapping, Set, Tuple

from benchmarks.secure_memory.canonical import canonical_sha256

from .contracts import (
    CanonicalEffect,
    EnforcementMode,
    GuardianDecision,
    RiskAssessment,
    RiskDisposition,
    RiskLevel,
    RiskStage,
)


_LOW_OPERATIONS = frozenset(
    {
        "workspace.hash",
        "workspace.list",
        "workspace.list_directory",
        "workspace.read",
        "workspace.read_file",
        "workspace.search",
        "workspace.stat",
    }
)
_MEDIUM_OPERATIONS = frozenset(
    {
        "workspace.apply_patch",
        "workspace.copy",
        "workspace.create_directory",
        "workspace.mkdir",
        "workspace.move",
        "workspace.patch",
        "workspace.rename",
        "workspace.write",
        "workspace.write_file",
    }
)
_HIGH_OPERATIONS = frozenset(
    {
        "workspace.delete",
        "workspace.delete_path",
        "workspace.execute",
        "workspace.network",
        "workspace.network.send",
        "workspace.network_request",
        "workspace.process.start",
        "workspace.publish",
        "workspace.remove",
        "workspace.run_process",
        "workspace.shell",
        "workspace.upload",
    }
)
KNOWN_OPERATIONS = _LOW_OPERATIONS | _MEDIUM_OPERATIONS | _HIGH_OPERATIONS

MANDATORY_CONSTRAINT_REGISTRY: Mapping[str, str] = {
    "CROSS_PROJECT_TARGET": "constraint.project-boundary",
    "EVIDENCE_TAMPERING": "constraint.evidence-immutability",
    "IRREVERSIBLE_DESTRUCTION": "constraint.recoverable-effects",
    "PATH_ESCAPE": "constraint.workspace-boundary",
    "SECRET_EXFILTRATION": "constraint.no-secret-exfiltration",
    "WORKSPACE_GATEWAY_BYPASS": "constraint.workspace-gateway-only",
}

_DESTRUCTIVE_MARKERS = ("delete", "remove", "truncate", "overwrite", "reset", "wipe")
_EGRESS_MARKERS = ("network", "send", "upload", "publish", "exfiltrate", "external")
_EVIDENCE_MARKERS = (
    "/evidence/",
    "/receipts/",
    "/trace/",
    "decision-closure",
    "requirement-ledger",
    "trusted-memory",
)
_PATH_ARGUMENT_KEYS = frozenset(
    {
        "cwd",
        "destination_path",
        "path",
        "source_path",
        "working_directory",
    }
)
_SECRET_ARGUMENT_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "api_token",
        "authorization",
        "bearer_token",
        "cookie",
        "credential",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
_SECRET_ARGUMENT_SUFFIXES = (
    "api_key",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def _walk_json(value: Any) -> Iterable[Tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json(item)


def _is_path_escape(path: str) -> bool:
    if not path or "\x00" in path or "\\" in path or _WINDOWS_DRIVE.match(path):
        return True
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return True
    return "//" in path or path.endswith("/")


def _has_path_escape(effect: CanonicalEffect) -> bool:
    target = effect.target.lower()
    candidates = [] if target.startswith(("http://", "https://")) else [effect.target]
    candidates.extend(
        value
        for key, value in _walk_json(effect.final_arguments)
        if key.lower() in _PATH_ARGUMENT_KEYS and isinstance(value, str)
    )
    return any(_is_path_escape(candidate) for candidate in candidates)


def _has_cross_project_target(effect: CanonicalEffect) -> bool:
    expected_scope = f"project:{effect.project_id}"
    project_scopes = {item for item in effect.affected_scope if item.startswith("project:")}
    if project_scopes and project_scopes != {expected_scope}:
        return True

    lowered = effect.target.lower()
    explicit_project_targets = (
        f"project:{effect.project_id.lower()}",
        f"projects/{effect.project_id.lower()}/",
    )
    mentions_project = "project:" in lowered or "projects/" in lowered
    return mentions_project and not any(marker in lowered for marker in explicit_project_targets)


def _has_secret_material(value: Any) -> bool:
    for key, item in _walk_json(value):
        lowered_key = key.lower().replace("-", "_")
        if (
            lowered_key in _SECRET_ARGUMENT_NAMES
            or lowered_key.startswith("authorization_")
            or any(lowered_key.endswith(f"_{suffix}") for suffix in _SECRET_ARGUMENT_SUFFIXES)
        ):
            return True
        if lowered_key in {"classification", "data_classification", "sensitivity"} and str(
            item
        ).upper() in {"SECRET", "CREDENTIAL", "PRIVATE_KEY"}:
            return True
    return False


def _is_secret_exfiltration(effect: CanonicalEffect) -> bool:
    operation = effect.operation.lower()
    is_egress = any(marker in operation for marker in _EGRESS_MARKERS)
    return is_egress and _has_secret_material(effect.final_arguments)


def _is_irreversible_destruction(effect: CanonicalEffect) -> bool:
    operation = effect.operation.lower()
    destructive = any(marker in operation for marker in _DESTRUCTIVE_MARKERS)
    irreversible = effect.reversibility.strip().upper() in {
        "IRREVERSIBLE",
        "NONE",
        "NOT_REVERSIBLE",
        "UNRECOVERABLE",
    }
    return destructive and irreversible


def _is_evidence_tampering(effect: CanonicalEffect) -> bool:
    operation = effect.operation.lower()
    mutating = operation not in _LOW_OPERATIONS
    target = f"/{effect.target.lower().strip('/')}"
    protected_target = any(marker in target for marker in _EVIDENCE_MARKERS)
    return mutating and protected_target


def _bypasses_workspace_gateway(effect: CanonicalEffect) -> bool:
    if not effect.operation.startswith("workspace."):
        return True
    for key, value in _walk_json(effect.final_arguments):
        if key.lower() in {"execution_route", "gateway", "route"} and str(value).upper() not in {
            "CONTROLLED_WORKSPACE",
            "WORKSPACE_GATEWAY",
        }:
            return True
        if key.lower() in {"agentteams_tool", "direct_tool"} and bool(value):
            return True
    return False


def mandatory_rule_matches(effect: CanonicalEffect) -> Tuple[Tuple[str, str], ...]:
    """Return the shared mandatory reason/constraint matches in canonical order."""

    matched: Dict[str, str] = {}
    checks = {
        "CROSS_PROJECT_TARGET": _has_cross_project_target(effect),
        "EVIDENCE_TAMPERING": _is_evidence_tampering(effect),
        "IRREVERSIBLE_DESTRUCTION": _is_irreversible_destruction(effect),
        "PATH_ESCAPE": _has_path_escape(effect),
        "SECRET_EXFILTRATION": _is_secret_exfiltration(effect),
        "WORKSPACE_GATEWAY_BYPASS": _bypasses_workspace_gateway(effect),
    }
    for reason, applies in checks.items():
        if applies:
            matched[reason] = MANDATORY_CONSTRAINT_REGISTRY[reason]
    return tuple(sorted(matched.items()))


def operation_risk_level(operation: str) -> RiskLevel:
    if operation in _LOW_OPERATIONS:
        return RiskLevel.LOW
    if operation in _MEDIUM_OPERATIONS:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


class EgoGuardian:
    """Independent deterministic Control-side review for system-HIGH effects."""

    RULE_VERSION = "agentteams-guardian-rules/2026-08-31"
    _RULE_TABLE = {
        "known_operations": tuple(sorted(KNOWN_OPERATIONS)),
        "mandatory_constraints": tuple(sorted(MANDATORY_CONSTRAINT_REGISTRY.items())),
        "unknown_operation": "HIGH/DENY",
        "known_high": "HIGH/APPROVAL_REQUIRED",
    }
    RULE_SHA256 = canonical_sha256("agentteams-guardian-rules", _RULE_TABLE)

    @classmethod
    def assess(
        cls,
        effect: CanonicalEffect,
        system_assessment: RiskAssessment,
        *,
        sequence: int,
    ) -> RiskAssessment:
        if system_assessment.stage is not RiskStage.SYSTEM:
            raise ValueError("Guardian requires a SYSTEM assessment")
        if system_assessment.effect_sha256 != effect.effect_sha256:
            raise ValueError("Guardian system assessment binds a different effect")
        if system_assessment.risk_level is not RiskLevel.HIGH:
            raise ValueError("Guardian may run only after a system HIGH assessment")

        matches = mandatory_rule_matches(effect)
        reasons: Set[str] = {reason for reason, _constraint in matches}
        constraints: Set[str] = {constraint for _reason, constraint in matches}
        required_constraints = set(system_assessment.mandatory_constraint_ids)
        known = effect.operation in KNOWN_OPERATIONS
        if not known:
            reasons.add("UNKNOWN_OPERATION")
            constraints.add("constraint.known-operation")

        if not required_constraints.issubset(constraints):
            reasons.add("SYSTEM_CONSTRAINT_MISMATCH")
            constraints.update(required_constraints)

        independent_level = operation_risk_level(effect.operation)
        if constraints:
            disposition = RiskDisposition.DENY
        elif independent_level is RiskLevel.HIGH:
            reasons.add("GUARDIAN_HIGH_IMPACT_EFFECT")
            disposition = RiskDisposition.APPROVAL_REQUIRED
        else:
            reasons.add("SYSTEM_GUARDIAN_RISK_MISMATCH")
            constraints.add("constraint.guardian-consistency")
            disposition = RiskDisposition.DENY

        return RiskAssessment(
            schema_version="agentteams-risk-assessment/v1",
            effect_sha256=effect.effect_sha256,
            stage=RiskStage.GUARDIAN,
            risk_level=RiskLevel.HIGH,
            disposition=disposition,
            reason_codes=tuple(sorted(reasons)),
            mandatory_constraint_ids=tuple(sorted(constraints)),
            rule_version=cls.RULE_VERSION,
            rule_sha256=cls.RULE_SHA256,
            sequence=sequence,
        )


def build_guardian_decision(
    effect: CanonicalEffect,
    system_assessment: RiskAssessment,
    *,
    enforcement_mode: EnforcementMode,
    guardian_sequence: int,
) -> GuardianDecision:
    """Build the exact Guardian decision, invoking Guardian only for system HIGH."""

    if system_assessment.stage is not RiskStage.SYSTEM:
        raise ValueError("Guardian decision requires a SYSTEM assessment")
    if system_assessment.effect_sha256 != effect.effect_sha256:
        raise ValueError("system assessment binds a different effect")

    guardian_assessment = None
    if system_assessment.risk_level is RiskLevel.HIGH:
        guardian_assessment = EgoGuardian.assess(
            effect,
            system_assessment,
            sequence=guardian_sequence,
        )
    effective = guardian_assessment or system_assessment
    values: Dict[str, Any] = {
        "schema_version": "agentteams-guardian-decision/v1",
        "effect_sha256": effect.effect_sha256,
        "system_assessment": system_assessment,
        "guardian_assessment": guardian_assessment,
        "enforcement_mode": enforcement_mode,
        "disposition": effective.disposition,
    }
    values["decision_sha256"] = canonical_sha256("agentteams-guardian-decision", values)
    return GuardianDecision.model_validate(values)


__all__ = [
    "EgoGuardian",
    "KNOWN_OPERATIONS",
    "MANDATORY_CONSTRAINT_REGISTRY",
    "build_guardian_decision",
    "mandatory_rule_matches",
    "operation_risk_level",
]
