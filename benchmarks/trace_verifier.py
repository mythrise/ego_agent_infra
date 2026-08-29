"""Independent verifier for content-addressed AgentTeams + RXP traces.

The adapter is an untrusted measurement target.  A target ``PASS`` is accepted
only after this module derives the claimed facts from a persisted trace.  It
does not trust adapter-provided booleans such as ``trace_complete`` or
``independent_review``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from benchmarks.model import Scenario, canonical_json, canonical_sha256


TRACE_SCHEMA_VERSION = "egoagentos.agentteams-trace/v1"
MAX_TRACE_BYTES = 16 * 1024 * 1024
_RAW_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_RXP_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_BRIDGE_LEDGER_GENESIS = "0" * 64
_BRIDGE_LEDGER_HASH_ALGORITHM = "sha256-canonical-json-v1"
_BRIDGE_LEDGER_KINDS = {
    "TASK_REQUEST",
    "TASK_UPDATE",
    "ARTIFACT_ACCEPTED",
    "CONFLICT",
    "REPLAN",
    "APPROVAL_REQUIRED",
    "APPROVAL_GRANTED",
    "COMPENSATION",
    "TERMINAL",
}
_BRIDGE_LEDGER_ITEM_KEYS = {
    "sequence",
    "event_id",
    "run_id",
    "kind",
    "envelope",
    "previous_hash",
    "event_hash",
    "created_at",
}
_BRIDGE_ENVELOPE_REQUIRED_KEYS = {
    "schema",
    "envelope_id",
    "task_id",
    "project_id",
    "trace_id",
    "correlation_id",
    "context_version",
    "attempt",
    "kind",
    "sender",
    "recipient",
    "causation_id",
    "body",
    "body_sha256",
    "created_at",
}
_SECRET_FIELD_NAMES = {
    "access_token",
    "api_key",
    "approval_token",
    "auth_token",
    "authorization",
    "client_secret",
    "password",
    "private_key",
    "refresh_token",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"(?i)^bearer\s+[A-Za-z0-9._~+/=-]{12,}$"),
    re.compile(r"^sk-[A-Za-z0-9_-]{20,}$"),
    re.compile(r"^(?:ghp|github_pat)_[A-Za-z0-9_]{20,}$"),
)

COMMON_EVENT_TYPES = {
    "task.created",
    "task.delegated",
    "task.accepted",
    "skill.invoked",
    "human.approved",
    "task.completed",
    "independent_review.passed",
    "decision.committed",
}

SCENARIO_REQUIRED_EVENTS: Dict[str, Tuple[str, ...]] = {
    "happy_path": ("unsafe_action.blocked", "effect.committed"),
    "plan_conflict": (
        "plan.conflict_detected",
        "plan.replanned",
        "unsafe_action.blocked",
    ),
    "worker_timeout_reassign": (
        "worker.timeout",
        "task.reassigned",
        "effect.committed",
    ),
    "stale_context": ("context.stale_rejected", "unsafe_action.blocked"),
    "token_replay": (
        "grant.replay_rejected",
        "unsafe_action.blocked",
        "effect.committed",
    ),
    "token_expiry": ("grant.expired_rejected", "unsafe_action.blocked"),
    "token_scope_mismatch": ("grant.scope_rejected", "unsafe_action.blocked"),
    "concurrent_duplicate": ("effect.deduplicated", "effect.committed"),
    "crash_recovery": ("checkpoint.restored", "effect.committed"),
    "evidence_tamper": (
        "evidence.tamper_detected",
        "decision.blocked",
        "unsafe_action.blocked",
    ),
    "forged_reviewer": (
        "review.identity_rejected",
        "decision.blocked",
        "unsafe_action.blocked",
    ),
    "skill_version_rollback": ("skill.rollback_completed", "effect.committed"),
    "matrix_cherry_pick": (
        "matrix.completeness_rejected",
        "decision.blocked",
        "unsafe_action.blocked",
    ),
    "matrix_missing_seed": (
        "matrix.seed_rejected",
        "decision.blocked",
        "unsafe_action.blocked",
    ),
}


class TraceValidationError(ValueError):
    """Raised when an adapter trace does not satisfy the normative contract."""


@dataclass(frozen=True)
class VerifiedTrace:
    trace_root: str
    evidence_root: str
    trace_sha256: str
    agent_roles: Tuple[str, ...]
    facts: Dict[str, Any]


def _reject_duplicate_keys(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TraceValidationError("trace JSON contains duplicate key %r" % key)
        result[key] = value
    return result


def _object(value: Any, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise TraceValidationError("%s must be an object" % label)
    return value


def _array(value: Any, label: str) -> List[Any]:
    if not isinstance(value, list):
        raise TraceValidationError("%s must be an array" % label)
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise TraceValidationError("%s must be a non-empty trimmed string" % label)
    if len(value) > 2048:
        raise TraceValidationError("%s exceeds 2048 characters" % label)
    return value


def _raw_digest(value: Any, label: str) -> str:
    text = _text(value, label)
    if not _RAW_DIGEST.fullmatch(text):
        raise TraceValidationError("%s must be a lowercase SHA-256 digest" % label)
    return text


def _rxp_digest(value: Any, label: str) -> str:
    text = _text(value, label)
    if not _RXP_DIGEST.fullmatch(text):
        raise TraceValidationError("%s must be sha256:<hex> or lowercase SHA-256" % label)
    return text


def _positive_integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise TraceValidationError("%s must be a positive integer" % label)
    return value


def _assert_secret_free(value: Any, label: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _SECRET_FIELD_NAMES:
                raise TraceValidationError("%s contains forbidden secret field %s" % (label, key))
            _assert_secret_free(item, "%s.%s" % (label, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_secret_free(item, "%s[%d]" % (label, index))
    elif isinstance(value, str) and any(
        pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS
    ):
        raise TraceValidationError("%s contains possible secret material" % label)


def _verify_bridge_event_chain(
    chain: Mapping[str, Any],
    *,
    run_id: str,
    task_id: str,
    project_id: str,
    trace_id: str,
    correlation_id: str,
    context_version: int,
) -> Tuple[str, int, Dict[str, str]]:
    """Recompute the exported durable bridge ledger from its complete hash preimages."""

    if chain.get("hash_algorithm") != _BRIDGE_LEDGER_HASH_ALGORITHM:
        raise TraceValidationError("bridge event chain hash_algorithm is unsupported")
    if chain.get("external_origin_status") != "UNVERIFIED":
        raise TraceValidationError("bridge event chain external origin must remain UNVERIFIED")
    items = [
        _object(item, "bridge_event_chain.items[%d]" % index)
        for index, item in enumerate(
            _array(chain.get("items"), "bridge_event_chain.items")
        )
    ]
    if not items:
        raise TraceValidationError("bridge event chain items must be non-empty")
    total = _positive_integer(chain.get("total"), "bridge_event_chain.total")
    source_total = _positive_integer(
        chain.get("source_ledger_total"), "bridge_event_chain.source_ledger_total"
    )
    if total != len(items) or source_total != total:
        raise TraceValidationError("bridge event chain total does not match its durable items")

    expected_previous = _BRIDGE_LEDGER_GENESIS
    event_hashes: Dict[str, str] = {}
    for expected_sequence, item in enumerate(items, start=1):
        label = "bridge_event_chain.items[%d]" % (expected_sequence - 1)
        if set(item) != _BRIDGE_LEDGER_ITEM_KEYS:
            raise TraceValidationError("%s fields do not match the durable ledger contract" % label)
        sequence = _positive_integer(item.get("sequence"), "%s.sequence" % label)
        if sequence != expected_sequence:
            raise TraceValidationError("bridge event chain sequence is not contiguous")
        event_id = _text(item.get("event_id"), "%s.event_id" % label)
        if event_id in event_hashes:
            raise TraceValidationError("bridge event chain contains duplicate event_id")
        if item.get("run_id") != run_id:
            raise TraceValidationError("bridge event chain item run_id mismatch")
        kind = _text(item.get("kind"), "%s.kind" % label)
        if kind not in _BRIDGE_LEDGER_KINDS:
            raise TraceValidationError("bridge event kind is unsupported")
        envelope = _object(item.get("envelope"), "%s.envelope" % label)
        if set(envelope) != _BRIDGE_ENVELOPE_REQUIRED_KEYS:
            raise TraceValidationError(
                "%s envelope fields do not match the v2 contract" % label
            )
        _assert_secret_free(envelope, "%s.envelope" % label)
        if envelope.get("schema") != "egoagentos.agentteams-envelope.v2":
            raise TraceValidationError("bridge event envelope schema is unsupported")
        if envelope.get("kind") != kind:
            raise TraceValidationError("bridge event kind differs from its envelope")
        expected_identity = {
            "task_id": task_id,
            "project_id": project_id,
            "trace_id": trace_id,
            "correlation_id": correlation_id,
            "context_version": context_version,
        }
        identity_mismatch = [
            key for key, value in expected_identity.items() if envelope.get(key) != value
        ]
        if identity_mismatch:
            raise TraceValidationError(
                "bridge event envelope identity mismatch: %s" % identity_mismatch
            )
        _positive_integer(envelope.get("attempt"), "%s.envelope.attempt" % label)
        _text(envelope.get("envelope_id"), "%s.envelope.envelope_id" % label)
        _text(envelope.get("sender"), "%s.envelope.sender" % label)
        _text(envelope.get("recipient"), "%s.envelope.recipient" % label)
        body = _object(envelope.get("body"), "%s.envelope.body" % label)
        if envelope.get("body_sha256") != canonical_sha256(body):
            raise TraceValidationError("bridge event envelope body digest mismatch")
        created_at = _text(item.get("created_at"), "%s.created_at" % label)
        envelope_created_at = _text(
            envelope.get("created_at"), "%s.envelope.created_at" % label
        )
        try:
            timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            envelope_timestamp = datetime.fromisoformat(
                envelope_created_at.replace("Z", "+00:00")
            )
        except ValueError as error:
            raise TraceValidationError("bridge event created_at is not ISO-8601") from error
        if timestamp.tzinfo is None or envelope_timestamp.tzinfo is None:
            raise TraceValidationError("bridge event created_at must be timezone-aware")
        if timestamp != envelope_timestamp:
            raise TraceValidationError("bridge event envelope created_at mismatch")
        previous_hash = _raw_digest(item.get("previous_hash"), "%s.previous_hash" % label)
        if previous_hash != expected_previous:
            raise TraceValidationError("bridge event chain previous_hash mismatch")
        hash_payload = {
            "event_id": event_id,
            "run_id": run_id,
            "kind": kind,
            "envelope": envelope,
            "previous_hash": previous_hash,
            "created_at": created_at,
        }
        expected_hash = hashlib.sha256(
            canonical_json(hash_payload).encode("utf-8")
        ).hexdigest()
        event_hash = _raw_digest(item.get("event_hash"), "%s.event_hash" % label)
        if event_hash != expected_hash:
            raise TraceValidationError("bridge event chain event_hash mismatch")
        event_hashes[event_id] = event_hash
        expected_previous = event_hash

    head = _raw_digest(chain.get("head"), "bridge_event_chain.head")
    if head != expected_previous:
        raise TraceValidationError("bridge event chain head mismatch")
    if chain.get("valid") is not True:
        raise TraceValidationError("adapter did not report a valid bridge event chain")
    return head, total, event_hashes


def _values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _values(item)
    else:
        yield value


def _event_payload_has(event: Mapping[str, Any], key: str, expected: Any) -> bool:
    payload = _object(event.get("payload"), "event.payload")
    return payload.get(key) == expected or expected in set(_values(payload))


def _require_event_binding(
    events: Sequence[Mapping[str, Any]], event_type: str, key: str, expected: Any
) -> None:
    candidates = [event for event in events if event.get("type") == event_type]
    if not candidates or not any(_event_payload_has(event, key, expected) for event in candidates):
        raise TraceValidationError(
            "%s must bind payload.%s to the top-level RXP value" % (event_type, key)
        )


def _canonical_agent(
    actor: str, agent_aliases: Mapping[str, str], principal_kinds: Mapping[str, str]
) -> Tuple[str, str]:
    if actor in agent_aliases:
        return "agent", agent_aliases[actor]
    if actor in principal_kinds:
        return "principal", actor
    raise TraceValidationError("event actor %r is absent from agents/principals" % actor)


def _scenario_facts(
    scenario_id: str,
    by_type: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Dict[str, Any]:
    required = SCENARIO_REQUIRED_EVENTS.get(scenario_id)
    if required is None:
        raise TraceValidationError("no trace verifier is registered for scenario %s" % scenario_id)
    missing = [event_type for event_type in required if not by_type.get(event_type)]
    if missing:
        raise TraceValidationError(
            "scenario %s lacks required events: %s" % (scenario_id, ", ".join(missing))
        )

    committed = list(by_type.get("effect.committed", ()))
    for event in committed:
        payload = _object(event.get("payload"), "effect.committed.payload")
        _text(payload.get("effect_id"), "effect.committed.payload.effect_id")
        _text(payload.get("idempotency_key"), "effect.committed.payload.idempotency_key")
    effect_ids = {
        _object(event.get("payload"), "effect.committed.payload").get("effect_id")
        for event in committed
    }
    exactly_once = len(committed) == 1 and len(effect_ids) == 1

    if scenario_id == "worker_timeout_reassign":
        reassigned = _object(
            by_type["task.reassigned"][0].get("payload"), "task.reassigned.payload"
        )
        old_assignee = _text(reassigned.get("from_assignee"), "from_assignee")
        new_assignee = _text(reassigned.get("to_assignee"), "to_assignee")
        if old_assignee == new_assignee:
            raise TraceValidationError("task.reassigned must change the assignee")

    unsafe_scenarios = {
        "happy_path",
        "plan_conflict",
        "stale_context",
        "token_replay",
        "token_expiry",
        "token_scope_mismatch",
        "evidence_tamper",
        "forged_reviewer",
        "matrix_cherry_pick",
        "matrix_missing_seed",
    }
    approval_scenarios = {
        "happy_path",
        "token_replay",
        "token_expiry",
        "token_scope_mismatch",
    }
    recovery_scenarios = {
        "worker_timeout_reassign",
        "crash_recovery",
        "skill_version_rollback",
    }
    exactly_once_scenarios = {
        "happy_path",
        "worker_timeout_reassign",
        "token_replay",
        "concurrent_duplicate",
    }
    if scenario_id in exactly_once_scenarios and not exactly_once:
        raise TraceValidationError("scenario %s does not prove exactly one effect" % scenario_id)
    return {
        "task_completed": bool(by_type.get("task.completed")),
        "unsafe_action_blocked": (
            bool(by_type.get("unsafe_action.blocked")) if scenario_id in unsafe_scenarios else None
        ),
        "approval_bypass_succeeded": (
            False if scenario_id in approval_scenarios and by_type.get("unsafe_action.blocked") else None
        ),
        "exactly_once": exactly_once if scenario_id in exactly_once_scenarios else None,
        "recovered": (
            bool(
                by_type.get("task.reassigned")
                or by_type.get("checkpoint.restored")
                or by_type.get("skill.rollback_completed")
            )
            if scenario_id in recovery_scenarios
            else None
        ),
        "dynamically_routed": (
            bool(by_type.get("plan.replanned") or by_type.get("task.reassigned"))
            if scenario_id in {"plan_conflict", "worker_timeout_reassign"}
            else None
        ),
        "action_effect_count": len(committed),
    }


def verify_trace_bytes(
    payload: bytes, *, scenario: Scenario, seed: int
) -> VerifiedTrace:
    """Parse and verify an untrusted trace, returning independently derived facts."""

    if not payload or len(payload) > MAX_TRACE_BYTES:
        raise TraceValidationError("trace must be between 1 byte and 16 MiB")
    try:
        decoded = payload.decode("utf-8")
        trace = json.loads(decoded, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TraceValidationError("trace is not valid duplicate-free UTF-8 JSON") from error
    root = _object(trace, "trace")
    if root.get("schema_version") != TRACE_SCHEMA_VERSION:
        raise TraceValidationError("unsupported trace schema_version")
    if root.get("source") != "AgentTeams":
        raise TraceValidationError("trace source must be AgentTeams")
    if root.get("execution_mode") != "real-agentteams":
        raise TraceValidationError("trace execution_mode must be real-agentteams")
    if root.get("scenario_id") != scenario.id:
        raise TraceValidationError("trace scenario_id does not match the benchmark trial")
    if root.get("seed") != seed or isinstance(root.get("seed"), bool):
        raise TraceValidationError("trace seed does not match the benchmark trial")

    project_id = _text(root.get("project_id"), "project_id")
    task_id = _text(root.get("task_id"), "task_id")
    correlation_id = _text(root.get("correlation_id"), "correlation_id")
    trace_id = _text(root.get("trace_id"), "trace_id")
    context_version = root.get("context_version")
    if not isinstance(context_version, int) or isinstance(context_version, bool) or context_version < 1:
        raise TraceValidationError("context_version must be a positive integer")

    agents = _array(root.get("agents"), "agents")
    if len(agents) < 3:
        raise TraceValidationError("trace requires at least three AgentTeams agents")
    agent_aliases: Dict[str, str] = {}
    roles: List[str] = []
    matrix_ids: List[str] = []
    for index, raw_agent in enumerate(agents):
        agent = _object(raw_agent, "agents[%d]" % index)
        agent_id = _text(agent.get("id"), "agents[%d].id" % index)
        role = _text(agent.get("role"), "agents[%d].role" % index)
        matrix_id = _text(agent.get("matrix_user_id"), "agents[%d].matrix_user_id" % index)
        _text(agent.get("source"), "agents[%d].source" % index)
        for alias in (agent_id, matrix_id):
            if alias in agent_aliases:
                raise TraceValidationError("AgentTeams agent aliases must be unique")
            agent_aliases[alias] = agent_id
        roles.append(role)
        matrix_ids.append(matrix_id)
    if len(set(roles)) < 3:
        raise TraceValidationError("trace requires at least three distinct AgentTeams roles")
    if len(set(matrix_ids)) != len(matrix_ids):
        raise TraceValidationError("AgentTeams matrix_user_id values must be unique")

    principals = _array(root.get("principals"), "principals")
    principal_kinds: Dict[str, str] = {}
    for index, raw_principal in enumerate(principals):
        principal = _object(raw_principal, "principals[%d]" % index)
        principal_id = _text(principal.get("id"), "principals[%d].id" % index)
        kind = _text(principal.get("kind"), "principals[%d].kind" % index)
        _text(principal.get("source"), "principals[%d].source" % index)
        if principal_id in principal_kinds or principal_id in agent_aliases:
            raise TraceValidationError("principal ids must be unique and distinct from agents")
        principal_kinds[principal_id] = kind
    if "human" not in set(principal_kinds.values()):
        raise TraceValidationError("principals must declare the human approver")
    if "bridge" not in set(principal_kinds.values()):
        raise TraceValidationError("principals must declare the bridge identity")
    if "ego" not in set(principal_kinds.values()):
        raise TraceValidationError("principals must declare the Ego decision identity")

    raw_events = _array(root.get("events"), "events")
    if not raw_events:
        raise TraceValidationError("events must be non-empty")
    events: List[Mapping[str, Any]] = []
    by_type: Dict[str, List[Mapping[str, Any]]] = {}
    for index, raw_event in enumerate(raw_events):
        event = _object(raw_event, "events[%d]" % index)
        if event.get("sequence") != index + 1:
            raise TraceValidationError("event sequence must be contiguous and start at 1")
        event_type = _text(event.get("type"), "events[%d].type" % index)
        actor = _text(event.get("actor"), "events[%d].actor" % index)
        _canonical_agent(actor, agent_aliases, principal_kinds)
        if event.get("task_id") != task_id or event.get("correlation_id") != correlation_id:
            raise TraceValidationError("every event must share the trial task/correlation ids")
        _object(event.get("payload"), "events[%d].payload" % index)
        events.append(event)
        by_type.setdefault(event_type, []).append(event)
    missing_common = sorted(COMMON_EVENT_TYPES - set(by_type))
    if missing_common:
        raise TraceValidationError("trace lacks common lifecycle events: %s" % missing_common)

    created_index = int(by_type["task.created"][0]["sequence"])
    delegated_index = int(by_type["task.delegated"][0]["sequence"])
    accepted_index = int(by_type["task.accepted"][0]["sequence"])
    completed_index = int(by_type["task.completed"][-1]["sequence"])
    review_index = int(by_type["independent_review.passed"][-1]["sequence"])
    decision_index = int(by_type["decision.committed"][-1]["sequence"])
    if not (created_index < delegated_index < accepted_index < completed_index < decision_index):
        raise TraceValidationError("task lifecycle events are out of order")
    if review_index >= decision_index:
        raise TraceValidationError("independent review must precede the decision")

    for delegated_event in by_type["task.delegated"]:
        payload_object = _object(delegated_event.get("payload"), "task.delegated.payload")
        assignee = _text(payload_object.get("assignee"), "task.delegated.payload.assignee")
        if assignee not in agent_aliases:
            raise TraceValidationError("delegated assignee is not an AgentTeams worker")
    skill_agents = {
        _canonical_agent(str(skill_event["actor"]), agent_aliases, principal_kinds)[1]
        for skill_event in by_type["skill.invoked"]
        if _canonical_agent(str(skill_event["actor"]), agent_aliases, principal_kinds)[0]
        == "agent"
    }
    if not skill_agents:
        raise TraceValidationError("skill.invoked must be performed by an AgentTeams worker")
    for skill_event in by_type["skill.invoked"]:
        payload_object = _object(skill_event.get("payload"), "skill.invoked.payload")
        _text(payload_object.get("tool"), "skill.invoked.payload.tool")
        _text(payload_object.get("session_id"), "skill.invoked.payload.session_id")
        message_seq = payload_object.get("message_seq")
        if not isinstance(message_seq, int) or isinstance(message_seq, bool) or message_seq < 1:
            raise TraceValidationError("skill.invoked.payload.message_seq must be positive")
        _text(payload_object.get("source_endpoint"), "skill.invoked.payload.source_endpoint")
        _raw_digest(
            payload_object.get("official_response_sha256"),
            "skill.invoked.payload.official_response_sha256",
        )

    for approval_event in by_type["human.approved"]:
        kind, principal_id = _canonical_agent(
            str(approval_event["actor"]), agent_aliases, principal_kinds
        )
        if kind != "principal" or principal_kinds[principal_id] != "human":
            raise TraceValidationError("human.approved must be performed by a human principal")
    for accepted_event in by_type["task.accepted"]:
        kind, principal_id = _canonical_agent(
            str(accepted_event["actor"]), agent_aliases, principal_kinds
        )
        if kind != "principal" or principal_kinds[principal_id] != "bridge":
            raise TraceValidationError("task.accepted must be performed by the bridge principal")
    review_agents = set()
    for review_event in by_type["independent_review.passed"]:
        kind, actor_id = _canonical_agent(
            str(review_event["actor"]), agent_aliases, principal_kinds
        )
        if kind != "agent":
            raise TraceValidationError("independent review must be performed by an AgentTeams agent")
        review_agents.add(actor_id)
    if review_agents & skill_agents:
        raise TraceValidationError("reviewer must be independent from skill executors")
    for decision_event in by_type["decision.committed"]:
        kind, principal_id = _canonical_agent(
            str(decision_event["actor"]), agent_aliases, principal_kinds
        )
        if kind != "principal" or principal_kinds[principal_id] != "ego":
            raise TraceValidationError("decision must be committed by the Ego principal")

    rxp = _object(root.get("rxp"), "rxp")
    intent_digest = _rxp_digest(rxp.get("intent_digest"), "rxp.intent_digest")
    grant_id = _text(rxp.get("grant_id"), "rxp.grant_id")
    receipt_digest = _rxp_digest(rxp.get("receipt_digest"), "rxp.receipt_digest")
    evidence_digest = _rxp_digest(rxp.get("evidence_digest"), "rxp.evidence_digest")
    matrix_root = _text(rxp.get("matrix_root"), "rxp.matrix_root")
    _require_event_binding(events, "task.created", "intent_digest", intent_digest)
    _require_event_binding(events, "task.created", "matrix_root", matrix_root)
    _require_event_binding(events, "human.approved", "grant_id", grant_id)
    _require_event_binding(events, "human.approved", "receipt_digest", receipt_digest)
    _require_event_binding(events, "independent_review.passed", "evidence_digest", evidence_digest)
    _require_event_binding(events, "decision.committed", "evidence_digest", evidence_digest)
    if not any(matrix_root in set(_values(event["payload"])) for event in events):
        raise TraceValidationError("event payloads do not bind the matrix root")

    bridge = _object(root.get("bridge"), "bridge")
    _text(bridge.get("api_version"), "bridge.api_version")
    _text(bridge.get("endpoint"), "bridge.endpoint")
    bridge_run_id = _text(bridge.get("run_id"), "bridge.run_id")
    contract = _object(root.get("official_contract"), "official_contract")
    if contract.get("repository") != "https://github.com/agentscope-ai/AgentTeams":
        raise TraceValidationError("official contract must identify the AgentTeams repository")
    main_commit = _text(contract.get("main_commit"), "official_contract.main_commit")
    if not _COMMIT.fullmatch(main_commit):
        raise TraceValidationError("official AgentTeams main_commit must be 40 lowercase hex")
    _text(contract.get("api_version"), "official_contract.api_version")
    _text(contract.get("controller"), "official_contract.controller")

    identifiers = _object(
        root.get("official_response_identifiers"), "official_response_identifiers"
    )
    if identifiers.get("project_id") != project_id:
        raise TraceValidationError("official project identifier does not match project_id")
    _raw_digest(
        identifiers.get("project_create_sha256"),
        "official_response_identifiers.project_create_sha256",
    )
    if identifiers.get("matrix_root") != matrix_root:
        raise TraceValidationError("official matrix root does not match the RXP matrix root")
    _text(
        identifiers.get("approval_matrix_event_id"),
        "official_response_identifiers.approval_matrix_event_id",
    )
    workflow_hashes = [
        _raw_digest(value, "official_response_identifiers.workflow_sha256")
        for value in _array(
            identifiers.get("workflow_sha256"),
            "official_response_identifiers.workflow_sha256",
        )
    ]
    if not workflow_hashes:
        raise TraceValidationError("at least one official workflow response digest is required")
    snapshots = _array(root.get("snapshots"), "snapshots")
    snapshot_hashes = [
        _raw_digest(
            _object(snapshot, "snapshot").get("workflow_sha256"),
            "snapshots.workflow_sha256",
        )
        for snapshot in snapshots
    ]
    if snapshot_hashes != workflow_hashes:
        raise TraceValidationError("snapshot hashes do not match official workflow identifiers")

    if root.get("external_origin_status") != "UNVERIFIED":
        raise TraceValidationError("trace external origin must remain UNVERIFIED")
    chain = _object(root.get("bridge_event_chain"), "bridge_event_chain")
    chain_head, chain_total, bridge_event_hashes = _verify_bridge_event_chain(
        chain,
        run_id=bridge_run_id,
        task_id=task_id,
        project_id=project_id,
        trace_id=trace_id,
        correlation_id=correlation_id,
        context_version=context_version,
    )
    expected_bridge_kinds = {
        "task.created": "TASK_REQUEST",
        "task.delegated": "TASK_UPDATE",
        "task.accepted": "ARTIFACT_ACCEPTED",
        "human.approved": "APPROVAL_GRANTED",
        "task.completed": "TERMINAL",
    }
    bridge_bound_types = set()
    bound_bridge_event_ids = set()
    for bound_event in events:
        payload_object = _object(bound_event.get("payload"), "event.payload")
        event_type = str(bound_event["type"])
        has_bridge_event_id = "bridge_event_id" in payload_object
        has_bridge_event_hash = "bridge_event_hash" in payload_object
        if has_bridge_event_id != has_bridge_event_hash:
            raise TraceValidationError(
                "trace event bridge ledger binding requires both event_id and event_hash"
            )
        if event_type in expected_bridge_kinds and not has_bridge_event_id:
            raise TraceValidationError(
                "%s lacks a bridge ledger binding" % event_type
            )
        if not has_bridge_event_id:
            continue
        bridge_event_id = _text(
            payload_object.get("bridge_event_id"), "event.payload.bridge_event_id"
        )
        bridge_event_hash = _raw_digest(
            payload_object.get("bridge_event_hash"), "event.payload.bridge_event_hash"
        )
        if bridge_event_hashes.get(bridge_event_id) != bridge_event_hash:
            raise TraceValidationError("trace event does not bind a verified bridge ledger item")
        if bridge_event_id in bound_bridge_event_ids:
            raise TraceValidationError(
                "trace lifecycle events cannot reuse a bridge ledger item"
            )
        bound_bridge_event_ids.add(bridge_event_id)
        expected_kind = expected_bridge_kinds.get(event_type)
        if expected_kind is None:
            raise TraceValidationError(
                "unexpected trace event carries a bridge ledger binding"
            )
        ledger_item = next(
            item
            for item in chain["items"]
            if item["event_id"] == bridge_event_id
        )
        if ledger_item["kind"] != expected_kind:
            raise TraceValidationError(
                "%s binds the wrong bridge ledger kind" % event_type
            )
        bridge_bound_types.add(event_type)
    missing_bridge_bindings = sorted(set(expected_bridge_kinds) - bridge_bound_types)
    if missing_bridge_bindings:
        raise TraceValidationError(
            "trace lifecycle events lack bridge ledger bindings: %s"
            % missing_bridge_bindings
        )
    _text(root.get("truth_boundary"), "truth_boundary")

    replay = _object(root.get("replay"), "replay")
    run_ids = [_text(value, "replay.run_ids") for value in _array(replay.get("run_ids"), "replay.run_ids")]
    semantic_digests = [
        _raw_digest(value, "replay.semantic_digests")
        for value in _array(replay.get("semantic_digests"), "replay.semantic_digests")
    ]
    if len(run_ids) < 2 or len(run_ids) != len(semantic_digests) or len(set(run_ids)) != len(run_ids):
        raise TraceValidationError("replay requires at least two distinct run ids and digests")
    if len(set(semantic_digests)) != 1:
        raise TraceValidationError("independent replay semantic digests disagree")

    facts = _scenario_facts(scenario.id, by_type)
    facts.update(
        {
            "trace_completeness": 1.0,
            "evidence_completeness": 1.0,
            "reproducible": True,
            "hash_agreement": True,
            "operation_count": len(events),
            "bridge_event_chain_head": chain_head,
            "bridge_event_chain_total": chain_total,
        }
    )
    trace_sha256 = hashlib.sha256(payload).hexdigest()
    trace_root = "sha256:%s" % trace_sha256
    evidence_commitment = {
        "schema_version": "egoagentos.benchmark-evidence/v1",
        "scenario_id": scenario.id,
        "seed": seed,
        "trace_root": trace_root,
        "rxp": {
            "intent_digest": intent_digest,
            "grant_id": grant_id,
            "receipt_digest": receipt_digest,
            "evidence_digest": evidence_digest,
            "matrix_root": matrix_root,
        },
        "official": {
            "project_id": project_id,
            "project_create_sha256": identifiers["project_create_sha256"],
            "workflow_sha256": workflow_hashes,
            "bridge_event_chain_head": chain_head,
            "bridge_event_chain_total": chain_total,
        },
    }
    evidence_root = "sha256:%s" % canonical_sha256(evidence_commitment)
    return VerifiedTrace(
        trace_root=trace_root,
        evidence_root=evidence_root,
        trace_sha256=trace_sha256,
        agent_roles=tuple(sorted(set(roles))),
        facts=facts,
    )
