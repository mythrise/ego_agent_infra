from __future__ import annotations

import re
from enum import Enum
import hashlib
from typing import Any, Dict, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import Field, field_validator

from benchmarks.secure_memory.canonical import canonical_bytes, canonical_sha256
from benchmarks.secure_memory.models import Digest, StrictModel

from .contracts import (
    ApprovalDisclosure,
    EnforcementMode,
    GuardianDecision,
    RiskLevel,
    SafetyDecision,
    SafetyVerdict,
    UserMessageMode,
    UserStatusProjection,
    WorkHierarchy,
)


class StatusEventType(str, Enum):
    PROGRESS = "PROGRESS"
    RISK = "RISK"
    APPROVAL = "APPROVAL"
    SECURITY = "SECURITY"


class StatusStateCode(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class StatusLocale(str, Enum):
    EN_US = "en-US"
    ZH_CN = "zh-CN"


class AdmittedStatusEvent(StrictModel):
    """A structured, admission-proven event; free-form agent prose is forbidden."""

    schema_version: Literal["agentteams-admitted-status-event/v1"]
    event_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    event_type: StatusEventType
    state_code: StatusStateCode
    sequence: int = Field(ge=0)
    specialist_terms: Tuple[str, ...] = ()
    admitted: Literal[True]
    guardian_decision_sha256: Optional[Digest] = None
    safety_decision_sha256: Optional[Digest] = None

    @field_validator("specialist_terms")
    @classmethod
    def normalize_specialist_terms(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        if any(not value for value in values):
            raise ValueError("specialist_terms values must be non-empty")
        return tuple(sorted(set(values)))


_GLOSSARIES: Mapping[StatusLocale, Mapping[str, str]] = {
    StatusLocale.EN_US: {
        "CAS": "an update that succeeds only when the stored version still matches",
        "Evidence Gate": "the check that requires accepted proof before completion",
        "Guardian": "an independent deterministic safety review",
        "RLS": "database rules that isolate each tenant's rows",
        "Trace": "the admitted event record used to reconstruct this status",
        "POLICY_WRITE": "a policy-governed write that needs an exact safety decision",
        "UNKNOWN_OPERATION": "an operation outside the frozen allowlist",
        "SYSTEM_HIGH_IMPACT_EFFECT": "the system classifier marked the effect high impact",
        "READ_ONLY_WORKSPACE_EFFECT": "a read-only workspace operation",
        "REVERSIBLE_WORKSPACE_MUTATION": "a reversible workspace change with recovery",
        "GUARDIAN_HIGH_IMPACT_EFFECT": "Guardian confirmed a high-impact effect",
        "SYSTEM_CONSTRAINT_MISMATCH": "Guardian found a mandatory rule mismatch",
        "SYSTEM_GUARDIAN_RISK_MISMATCH": "system and Guardian risk assessments disagree",
        "CROSS_PROJECT_TARGET": "the effect targets another project",
        "EVIDENCE_TAMPERING": "the effect could alter immutable evidence",
        "IRREVERSIBLE_DESTRUCTION": "the effect could destroy data without recovery",
        "PATH_ESCAPE": "the effect leaves the managed workspace",
        "SECRET_EXFILTRATION": "the effect could disclose a credential",
        "WORKSPACE_GATEWAY_BYPASS": "the effect attempts to bypass the typed gateway",
    },
    StatusLocale.ZH_CN: {
        "CAS": "仅在已存版本仍匹配时才成功的更新",
        "Evidence Gate": "只有验收证据通过后才允许完成的检查",
        "Guardian": "独立且确定性的安全复核",
        "RLS": "在数据库中隔离各租户数据行的规则",
        "Trace": "用于重建当前状态的已准入事件记录",
        "POLICY_WRITE": "受策略约束的写入操作，需要精确的安全决策",
        "UNKNOWN_OPERATION": "不在冻结允许列表中的操作",
        "SYSTEM_HIGH_IMPACT_EFFECT": "系统分类器将该操作判为高影响",
        "READ_ONLY_WORKSPACE_EFFECT": "只读工作区操作",
        "REVERSIBLE_WORKSPACE_MUTATION": "带有恢复方案的可逆工作区变更",
        "GUARDIAN_HIGH_IMPACT_EFFECT": "Guardian 确认该操作为高影响",
        "SYSTEM_CONSTRAINT_MISMATCH": "Guardian 发现强制规则不匹配",
        "SYSTEM_GUARDIAN_RISK_MISMATCH": "系统评估与 Guardian 风险评估不一致",
        "CROSS_PROJECT_TARGET": "操作目标属于其他项目",
        "EVIDENCE_TAMPERING": "操作可能修改不可变证据",
        "IRREVERSIBLE_DESTRUCTION": "操作可能在无恢复方案时销毁数据",
        "PATH_ESCAPE": "操作离开受管工作区",
        "SECRET_EXFILTRATION": "操作可能泄露凭据",
        "WORKSPACE_GATEWAY_BYPASS": "操作试图绕过类型化工作区网关",
    },
}

_STATE_TEXT: Mapping[StatusLocale, Mapping[StatusStateCode, str]] = {
    StatusLocale.EN_US: {
        StatusStateCode.PENDING: "pending",
        StatusStateCode.ACTIVE: "active",
        StatusStateCode.COMPLETED: "completed with admitted evidence",
        StatusStateCode.BLOCKED: "blocked",
        StatusStateCode.FAILED: "failed",
    },
    StatusLocale.ZH_CN: {
        StatusStateCode.PENDING: "待处理",
        StatusStateCode.ACTIVE: "进行中",
        StatusStateCode.COMPLETED: "已有准入证据证明完成",
        StatusStateCode.BLOCKED: "已阻塞",
        StatusStateCode.FAILED: "已失败",
    },
}

_MODE_EVENT = {
    UserMessageMode.RISK: StatusEventType.RISK,
    UserMessageMode.APPROVAL: StatusEventType.APPROVAL,
    UserMessageMode.SECURITY: StatusEventType.SECURITY,
}
_EVENT_MODE = {event_type: mode for mode, event_type in _MODE_EVENT.items()}
_MODE_PRIORITY = {
    UserMessageMode.PROGRESS: 0,
    UserMessageMode.DETAIL: 0,
    UserMessageMode.RISK: 1,
    UserMessageMode.APPROVAL: 2,
    UserMessageMode.SECURITY: 3,
}
_SECRET_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "bearer_token",
    "credential",
    "credentials",
    "password",
    "passwd",
    "private_key",
    "secret",
}
_SECRET_KEY_MARKERS = ("secret", "password", "passwd", "credential", "api_key", "apikey", "token")
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{6,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _assert_no_secret(value: Any, path: str = "approval") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = _normalize_key(str(key))
            if normalized in _SECRET_KEYS or any(
                marker in normalized for marker in _SECRET_KEY_MARKERS
            ):
                raise ValueError(f"secret or credential key is forbidden at {path}.{key}")
            _assert_no_secret(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_no_secret(item, f"{path}[{index}]")
        return
    if isinstance(value, str) and any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
        raise ValueError(f"secret or credential material is forbidden at {path}")


def _ordered_nodes(hierarchy: WorkHierarchy, node_ids: Sequence[str]) -> Tuple[str, ...]:
    node_by_id = {node.node_id: node for node in hierarchy.nodes}

    def path_key(node_id: str) -> Tuple[Tuple[int, str], ...]:
        path: list[Tuple[int, str]] = []
        current = node_by_id[node_id]
        seen: set[str] = set()
        while current.node_id not in seen:
            seen.add(current.node_id)
            path.append((current.order, current.node_id))
            if current.parent_id not in node_by_id:
                break
            current = node_by_id[current.parent_id]
        return tuple(reversed(path))

    return tuple(sorted(node_ids, key=path_key))


def _effective_mode(
    requested_mode: UserMessageMode,
    events: Sequence[AdmittedStatusEvent],
) -> UserMessageMode:
    mode = requested_mode
    for event in events:
        candidate = _EVENT_MODE.get(event.event_type)
        if candidate is not None and _MODE_PRIORITY[candidate] > _MODE_PRIORITY[mode]:
            mode = candidate
    return mode


def _validate_events(
    tenant_id: str,
    project_id: str,
    hierarchy: WorkHierarchy,
    events: Sequence[AdmittedStatusEvent],
) -> None:
    event_ids = tuple(event.event_id for event in events)
    if len(set(event_ids)) != len(event_ids):
        raise ValueError("source events contain a duplicate event ID")
    node_ids = {node.node_id for node in hierarchy.nodes}
    for event in events:
        if not isinstance(event, AdmittedStatusEvent):
            raise TypeError("source_events must contain AdmittedStatusEvent values")
        if event.tenant_id != tenant_id:
            raise ValueError("source event tenant does not match projection tenant")
        if event.project_id != project_id:
            raise ValueError("source event project does not match projection project")
        if event.node_id not in node_ids:
            raise ValueError("source event node is not present in the work hierarchy")


def _validate_safety_binding(
    mode: UserMessageMode,
    events: Sequence[AdmittedStatusEvent],
    guardian: Optional[GuardianDecision],
    safety: Optional[SafetyDecision],
    disclosure: Optional[ApprovalDisclosure],
) -> None:
    safety_mode = mode in {UserMessageMode.APPROVAL, UserMessageMode.SECURITY}
    supplied = (guardian, safety)
    if safety_mode and any(value is None for value in supplied):
        raise ValueError("approval/security status requires exact Guardian and Safety binding")
    if not safety_mode and any(value is not None for value in supplied):
        raise ValueError("normal/risk status forbids an unsolicited safety binding")
    if not safety_mode:
        if disclosure is not None:
            raise ValueError("approval disclosure is forbidden outside approval mode")
        return

    assert guardian is not None
    assert safety is not None
    if safety.guardian_decision != guardian:
        raise ValueError("Safety decision has a stale Guardian binding")
    relevant_type = _MODE_EVENT[mode]
    relevant = tuple(event for event in events if event.event_type is relevant_type)
    if not relevant:
        raise ValueError("safety override requires an admitted bound source event")
    for event in relevant:
        if event.guardian_decision_sha256 != guardian.decision_sha256:
            raise ValueError("source event has a stale Guardian digest binding")
        if event.safety_decision_sha256 != safety.decision_sha256:
            raise ValueError("source event has a stale Safety digest binding")
    _assert_no_secret(
        {
            "safe_arguments": safety.effect.final_arguments,
            "target": safety.effect.target,
            "recovery_plan": safety.effect.recovery_plan,
        }
    )

    if mode is UserMessageMode.APPROVAL:
        system = guardian.system_assessment
        independent = guardian.guardian_assessment
        if not (
            guardian.enforcement_mode is EnforcementMode.ENFORCING
            and system.risk_level is RiskLevel.HIGH
            and independent is not None
            and independent.risk_level is RiskLevel.HIGH
            and safety.verdict is SafetyVerdict.APPROVAL_REQUIRED
            and safety.approval_pending
            and disclosure is not None
            and safety.approval_disclosure == disclosure
        ):
            raise ValueError("approval status requires the exact enforcing double-HIGH chain")
    elif disclosure is not None:
        raise ValueError("approval disclosure is forbidden outside approval mode")


def _latest_by_node(
    events: Sequence[AdmittedStatusEvent], node_ids: Sequence[str]
) -> Dict[str, AdmittedStatusEvent]:
    allowed = set(node_ids)
    selected: Dict[str, AdmittedStatusEvent] = {}
    for event in sorted(events, key=lambda item: (item.sequence, item.event_id)):
        if event.node_id in allowed:
            selected[event.node_id] = event
    return selected


def _explained_terms(
    events: Sequence[AdmittedStatusEvent], locale: StatusLocale
) -> Tuple[Tuple[str, str], ...]:
    terms = tuple(sorted({term for event in events for term in event.specialist_terms}))
    glossary = _GLOSSARIES[locale]
    missing = tuple(term for term in terms if term not in glossary)
    if missing:
        raise ValueError(f"specialist term is missing from the {locale.value} glossary: {missing}")
    return tuple((term, glossary[term]) for term in terms)


def _state_line(
    node_ids: Sequence[str],
    latest: Mapping[str, AdmittedStatusEvent],
    locale: StatusLocale,
) -> str:
    unknown = "unknown" if locale is StatusLocale.EN_US else "未知"
    values = []
    for node_id in node_ids:
        event = latest.get(node_id)
        state = _STATE_TEXT[locale][event.state_code] if event is not None else unknown
        values.append(f"{node_id}={state}")
    return "; ".join(values)


def _render_status(
    mode: UserMessageMode,
    hierarchy: WorkHierarchy,
    visible_node_ids: Tuple[str, ...],
    attention_node_ids: Tuple[str, ...],
    latest: Mapping[str, AdmittedStatusEvent],
    explained_terms: Tuple[Tuple[str, str], ...],
    locale: StatusLocale,
    safety: Optional[SafetyDecision],
    disclosure: Optional[ApprovalDisclosure],
) -> str:
    state_line = _state_line(visible_node_ids, latest, locale)
    attention_locations = ", ".join(attention_node_ids)
    terms = "; ".join(f"{term} ({text})" for term, text in explained_terms)
    children = " or ".join(hierarchy.direct_child_ids)
    if locale is StatusLocale.EN_US:
        if mode in {UserMessageMode.PROGRESS, UserMessageMode.DETAIL}:
            result = f"Result: {hierarchy.current_node_id} status is available."
            next_step = (
                f"Next step: request DETAIL for {children}."
                if children
                else "Next step: no deeper admitted work item is available."
            )
        else:
            result = f"Result: {mode.value} requires attention at {attention_locations}."
            next_step = f"Next step: inspect {mode.value} at {attention_locations}."
        sections = [result, f"Current state: {state_line}.", next_step]
        if terms:
            sections.append(f"Terms: {terms}.")
    else:
        if mode in {UserMessageMode.PROGRESS, UserMessageMode.DETAIL}:
            result = f"结果：{hierarchy.current_node_id} 的状态已可查看。"
            next_step = (
                f"下一步：可对 {children} 请求 DETAIL。"
                if children
                else "下一步：没有可继续下钻的已准入工作项。"
            )
        else:
            result = f"结果：{attention_locations} 出现 {mode.value}，需要立即关注。"
            next_step = f"下一步：检查 {attention_locations} 的 {mode.value}。"
        sections = [result, f"当前状态：{state_line}。", next_step]
        if terms:
            sections.append(f"术语：{terms}。")

    if mode is UserMessageMode.APPROVAL:
        assert safety is not None
        assert disclosure is not None
        operation = safety.effect.operation
        arguments = canonical_bytes(disclosure.safe_arguments).decode("utf-8")
        scope = ",".join(disclosure.affected_scope)
        reasons = ",".join(disclosure.reason_codes)
        sections.append(
            "Approval: "
            f"operation={operation}; safe_args={arguments}; target={disclosure.target}; "
            f"affected_scope={scope}; reasons={reasons}; "
            f"recovery={disclosure.recovery_plan}; expiry={disclosure.expires_at_sequence}; "
            "choices=APPROVE|DENY."
        )
    elif mode is UserMessageMode.SECURITY:
        assert safety is not None
        sections.append(
            "Security: "
            f"affected_boundary={','.join(attention_node_ids)}; impact=integrity risk; "
            "containment=effect blocked; "
            f"recovery={safety.effect.recovery_plan}; required_decision=review safety event."
        )
    return " ".join(sections)


def project_user_status(
    *,
    tenant_id: str,
    project_id: str,
    task_id: str,
    hierarchy: WorkHierarchy,
    source_events: Sequence[AdmittedStatusEvent],
    requested_mode: UserMessageMode,
    locale: StatusLocale | str,
    guardian_decision: Optional[GuardianDecision] = None,
    safety_decision: Optional[SafetyDecision] = None,
    approval_disclosure: Optional[ApprovalDisclosure] = None,
) -> UserStatusProjection:
    """Render deterministic user status from admitted structured events only."""

    if not tenant_id or not project_id or not task_id:
        raise ValueError("tenant_id, project_id, and task_id must be non-empty")
    try:
        selected_locale = locale if isinstance(locale, StatusLocale) else StatusLocale(locale)
    except ValueError as exc:
        raise ValueError("locale must be exactly en-US or zh-CN") from exc
    events = tuple(source_events)
    _validate_events(tenant_id, project_id, hierarchy, events)
    mode = _effective_mode(requested_mode, events)
    _validate_safety_binding(
        mode,
        events,
        guardian_decision,
        safety_decision,
        approval_disclosure,
    )

    base_ids = (hierarchy.current_node_id,) + hierarchy.direct_child_ids
    override_type = _MODE_EVENT.get(mode)
    override_ids: Tuple[str, ...] = ()
    attention_ids: Tuple[str, ...] = ()
    if override_type is not None:
        # A higher-priority approval/security event must never hide a concurrent
        # lower-priority risk event. All safety descendants remain visible.
        safety_types = frozenset(_MODE_EVENT.values())
        attention_candidates = {
            event.node_id for event in events if event.event_type in safety_types
        }
        attention_ids = _ordered_nodes(hierarchy, tuple(attention_candidates))
        override_ids = tuple(node_id for node_id in attention_ids if node_id not in base_ids)
    visible_ids = base_ids + override_ids
    latest = _latest_by_node(events, visible_ids)
    used_events = tuple(
        event
        for event in events
        if latest.get(event.node_id) == event
        or (override_type is not None and event.event_type in frozenset(_MODE_EVENT.values()))
    )
    if safety_decision is not None:
        safety_terms: set[str] = set()
        for assessment in (
            safety_decision.guardian_decision.system_assessment,
            safety_decision.guardian_decision.guardian_assessment,
        ):
            if assessment is not None:
                safety_terms.update(assessment.reason_codes)
        generated_terms: Tuple[str, ...] = tuple(sorted(safety_terms))
        used_events = tuple(used_events)
    else:
        generated_terms = ()
    explained_terms = _explained_terms(used_events, selected_locale)
    if generated_terms:
        glossary = _GLOSSARIES[selected_locale]
        missing: Tuple[str, ...] = tuple(term for term in generated_terms if term not in glossary)
        if missing:
            raise ValueError(
                f"specialist term is missing from the {selected_locale.value} glossary: {missing}"
            )
        explained_terms = tuple(
            sorted(set(explained_terms).union({(term, glossary[term]) for term in generated_terms}))
        )
    status_text = _render_status(
        mode,
        hierarchy,
        visible_ids,
        attention_ids,
        latest,
        explained_terms,
        selected_locale,
        safety_decision,
        approval_disclosure,
    )
    projection_core = {
        "schema_version": "agentteams-user-status-projection/v1",
        "tenant_id": tenant_id,
        "project_id": project_id,
        "task_id": task_id,
        "mode": mode,
        "hierarchy": hierarchy,
        "visible_node_ids": visible_ids,
        "override_node_ids": override_ids,
        "status_text": status_text,
        "guardian_decision": guardian_decision,
        "guardian_decision_sha256": (
            guardian_decision.decision_sha256 if guardian_decision is not None else None
        ),
        "safety_decision": safety_decision,
        "safety_decision_sha256": (
            safety_decision.decision_sha256 if safety_decision is not None else None
        ),
        "approval_disclosure": approval_disclosure,
        "explained_terms": explained_terms,
        "source_event_ids": tuple(sorted(event.event_id for event in used_events)),
        "source_event_digests": tuple(
            hashlib.sha256(canonical_bytes(event.model_dump(mode="python"))).hexdigest()
            for event in sorted(used_events, key=lambda item: item.event_id)
        ),
    }
    return UserStatusProjection(
        schema_version="agentteams-user-status-projection/v1",
        tenant_id=tenant_id,
        project_id=project_id,
        task_id=task_id,
        mode=mode,
        hierarchy=hierarchy,
        visible_node_ids=visible_ids,
        override_node_ids=override_ids,
        status_text=status_text,
        guardian_decision=guardian_decision,
        guardian_decision_sha256=(
            guardian_decision.decision_sha256 if guardian_decision is not None else None
        ),
        safety_decision=safety_decision,
        safety_decision_sha256=(
            safety_decision.decision_sha256 if safety_decision is not None else None
        ),
        approval_disclosure=approval_disclosure,
        explained_terms=explained_terms,
        source_event_ids=tuple(sorted(event.event_id for event in used_events)),
        source_event_digests=tuple(
            hashlib.sha256(canonical_bytes(event.model_dump(mode="python"))).hexdigest()
            for event in sorted(used_events, key=lambda item: item.event_id)
        ),
        projection_sha256=canonical_sha256("agentteams-user-status-projection", projection_core),
    )


__all__ = [
    "AdmittedStatusEvent",
    "StatusEventType",
    "StatusLocale",
    "StatusStateCode",
    "project_user_status",
]
