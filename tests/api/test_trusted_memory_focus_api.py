from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.store import SQLiteStore
from apps.api.trusted_memory.focus_contracts import (
    FocusEvidenceRef,
    FocusMemoryQuery,
    TrustedFocusFact,
    TrustedMemoryFocusSource,
    build_trusted_memory_focus_source,
)
from apps.api.trusted_memory.focus_service import TrustedMemoryFocusService
from apps.api.trusted_memory.models import (
    DecisionOutcome,
    FactProvenance,
    MemoryOrigin,
    MemoryScope,
    MemoryState,
    TrustedFact,
)
from benchmarks.secure_memory.canonical import canonical_sha256
from benchmarks.secure_memory.models import FactScope, SourceRef, TrustedFactCore


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SERVICE_TOKEN = "focus-memory-test-token-32-bytes-minimum"
FOCUS_PATH = "/api/v1/internal/trusted-memory/focus"


def _query(**overrides: object) -> FocusMemoryQuery:
    values: dict[str, object] = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "outcomes": (DecisionOutcome.KEEP, DecisionOutcome.DROP),
        "origins": (MemoryOrigin.ATTESTED_EXTERNAL, MemoryOrigin.LOCAL_TRUSTED),
        "max_items": 16,
        "scan_limit": 128,
    }
    values.update(overrides)
    return FocusMemoryQuery.model_validate(values)


def _source_fact(
    digest: str = SHA_B,
    *,
    statement: str = "Keep the Evidence Gate before accepting a completed task.",
    fact_kind: str = "safety_constraint",
) -> TrustedFocusFact:
    return TrustedFocusFact(
        fact_sha256=digest,
        lineage_id="lineage-%s" % digest[:8],
        revision_id="revision-%s" % digest[:8],
        revision=1,
        fact_kind=fact_kind,
        statement=statement,
        component="agentteams-bridge",
        version="v1",
        outcome=DecisionOutcome.KEEP,
        origin=MemoryOrigin.LOCAL_TRUSTED,
        evidence=(
            FocusEvidenceRef(
                evidence_id="evidence-%s" % digest[:8],
                evidence_digest=SHA_A,
            ),
        ),
        closure_digest=SHA_C,
        provenance_sha256=SHA_D,
        projection_event_hash=digest,
    )


def _source(query: Optional[FocusMemoryQuery] = None) -> TrustedMemoryFocusSource:
    selected_query = query or _query()
    return build_trusted_memory_focus_source(
        selected_query,
        (_source_fact(),),
        scanned_count=1,
        truncated_by_scan_limit=False,
    )


class _FakeFocusService:
    def __init__(self) -> None:
        self.last_query: Optional[FocusMemoryQuery] = None

    def fetch(self, query: FocusMemoryQuery) -> TrustedMemoryFocusSource:
        self.last_query = query
        return _source(query)


def _trusted_fact(
    suffix: str,
    *,
    project_id: str = "project-a",
    fact_kind: str = "procedural",
) -> TrustedFact:
    scope = MemoryScope(
        tenant_id="tenant-a",
        project_id=project_id,
        component="agentteams-bridge",
        version="v1",
    )
    core = TrustedFactCore(
        schema_version="secure-memory-trusted-fact/v1",
        fact_id="fact-%s" % suffix,
        fact_kind=fact_kind,
        statement_utf8_base64=base64.b64encode(
            ("verified statement %s" % suffix).encode("utf-8")
        ).decode("ascii"),
        outcome=DecisionOutcome.KEEP.value,
        applicability_scope=FactScope(
            tenant_id="tenant-a",
            project_id=project_id,
            component="agentteams-bridge",
            version="v1",
            problem_id="problem-001",
        ),
        source_refs=(SourceRef(kind="evidence", identifier="evidence-%s" % suffix),),
        support_digests=(SHA_A,),
    )
    fact_digest = canonical_sha256("trusted-fact", core)
    provenance = FactProvenance(
        schema_version="egoagentos-fact-provenance/v1",
        scope=scope,
        task_id="task-001",
        generation=1,
        task_version=1,
        decision_id="decision-001",
        decision_digest=SHA_B,
        decision_closure_digest=SHA_C,
        origin=MemoryOrigin.LOCAL_TRUSTED,
        evaluator_id="sealed-evaluator",
        evaluator_result_digest=SHA_D,
        external_attestation_digest=None,
        verified_fact_digests=(fact_digest,),
        evidence_ids=("evidence-%s" % suffix,),
        evidence_digests=(SHA_A,),
        policy_version="memory-policy-v1",
        rule_version="memory-rule-v1",
    )
    values = {
        "schema_version": "egoagentos-trusted-memory-fact/v1",
        "revision_id": "revision-%s" % suffix,
        "lineage_id": "lineage-%s" % suffix,
        "revision": 1,
        "scope": scope,
        "outcome": DecisionOutcome.KEEP,
        "origin": MemoryOrigin.LOCAL_TRUSTED,
        "state": MemoryState.VALIDATED,
        "core": core,
        "trusted_fact_digest": fact_digest,
        "provenance": provenance,
    }
    return TrustedFact(
        **values,
        record_digest=canonical_sha256("trusted-memory-fact-record", values),
    )


def test_focus_api_fails_closed_when_service_token_is_not_configured(tmp_path: Path) -> None:
    application = create_app(
        str(tmp_path / "unconfigured.sqlite3"),
        trusted_memory_service_token="",
    )

    with TestClient(application) as client:
        response = client.post(FOCUS_PATH, json=_query().model_dump(mode="json"))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "trusted_memory_service_not_configured"


def test_focus_api_requires_exact_bearer_token_and_returns_digest_bound_source(
    tmp_path: Path,
) -> None:
    application = create_app(
        str(tmp_path / "authenticated.sqlite3"),
        trusted_memory_service_token=SERVICE_TOKEN,
    )
    fake = _FakeFocusService()
    application.state.trusted_memory_focus_service = fake
    query = _query()

    with TestClient(application) as client:
        missing = client.post(FOCUS_PATH, json=query.model_dump(mode="json"))
        wrong = client.post(
            FOCUS_PATH,
            json=query.model_dump(mode="json"),
            headers={"Authorization": "Bearer wrong-token"},
        )
        accepted = client.post(
            FOCUS_PATH,
            json=query.model_dump(mode="json"),
            headers={"Authorization": "Bearer %s" % SERVICE_TOKEN},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert accepted.status_code == 200
    payload = accepted.json()
    assert payload["source_sha256"] == _source(query).source_sha256
    assert payload["facts"][0]["statement"].startswith("Keep the Evidence Gate")
    assert SERVICE_TOKEN not in accepted.text
    assert fake.last_query == query


def test_focus_service_scans_only_current_eligible_facts_in_exact_project(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "focus-source.sqlite3"))
    wanted = _trusted_fact("wanted")
    other_project = _trusted_fact("other", project_id="project-other")
    for fact in (wanted, other_project):
        store.append_trusted_memory_record(
            tenant_id=fact.scope.tenant_id,
            project_id=fact.scope.project_id,
            lineage_id=fact.lineage_id,
            record=fact,
            idempotency_key="append-%s" % fact.lineage_id,
        )

    service = TrustedMemoryFocusService(store, tenant_id="tenant-a")
    result = service.fetch(_query(outcomes=(DecisionOutcome.KEEP,)))

    assert result.scanned_count == 1
    assert tuple(fact.lineage_id for fact in result.facts) == (wanted.lineage_id,)
    assert result.facts[0].statement == "verified statement wanted"
    assert result.facts[0].evidence[0].evidence_id == "evidence-wanted"
    assert len(result.memory_snapshot_root) == 64
    assert len(result.source_sha256) == 64
