from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from typing import Any, Dict, Tuple
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from apps.agentteams_bridge.errors import BridgeError
from apps.agentteams_bridge.models import (
    BridgeRun,
    CollaborationEnvelope,
    EnvelopeKind,
    ResearchTaskSpec,
    RunState,
)
from apps.agentteams_bridge.postgres_store import PostgresBridgeStore
from apps.agentteams_bridge.store import build_bridge_store
from tests.agentteams.test_bridge_extension_replay import (
    _binding,
    _populate_complete_authority,
    _run as _extension_run,
    _system_high,
)


def _run(run_id: str = "bridge_run_pg") -> BridgeRun:
    return BridgeRun(
        id=run_id,
        ego_task_id="ego_task_pg",
        agentteams_project_id="agentteams_project_%s" % run_id,
        team="ego-researchops",
        trace_id="trace_postgres_bridge",
        correlation_id="corr_postgres_bridge",
        context_version=1,
        state=RunState.PRE_APPROVAL,
        mode="live",
        objective="Exercise durable AgentTeams bridge persistence on PostgreSQL",
        task_graph=[
            ResearchTaskSpec(
                task_id="research_task_pg",
                title="PostgreSQL bridge contract",
                stage="PLAN",
                assigned_worker="ego-architect",
                assigned_to="@ego-architect:matrix.test",
            )
        ],
        checkpoint={"live": True, "nested": {"attempt": 1}},
        ack_timeout_seconds=30,
        execution_timeout_seconds=300,
        max_reassignments=2,
    )


def _envelope(run: BridgeRun, index: int) -> CollaborationEnvelope:
    return CollaborationEnvelope.build(
        task_id=run.ego_task_id,
        project_id=run.agentteams_project_id,
        trace_id=run.trace_id,
        correlation_id=run.correlation_id,
        context_version=run.context_version,
        kind=EnvelopeKind.TASK_UPDATE,
        sender="egoagentos-bridge",
        recipient="agentteams-team-leader",
        body={"index": index, "status": "in-progress"},
    )


def _login_url(postgres_url: str, user: str, password: str) -> str:
    parsed = urlsplit(postgres_url)
    host = parsed.hostname or "127.0.0.1"
    port = ":%d" % parsed.port if parsed.port else ""
    netloc = "%s:%s@%s%s" % (quote(user), quote(password), host, port)
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def test_explicit_bridge_url_factory_selects_postgres(postgres_url: str) -> None:
    store = build_bridge_store(database_url=postgres_url)
    assert store.engine == "postgresql"
    run = store.create_run(_run())
    assert store.get_run(run.id) == run


def test_bridge_store_restarts_from_jsonb_checkpoint_and_replays_migration_once(
    postgres_url: str,
) -> None:
    first = PostgresBridgeStore(postgres_url)
    run = first.create_run(_run())
    checkpoint = {**run.checkpoint, "restart_marker": "persisted"}
    updated = first.update_run(
        run.model_copy(update={"checkpoint": checkpoint}), expected_version=run.version
    )
    non_utc_envelope = _envelope(run, 1).model_copy(
        update={"created_at": datetime(2026, 8, 29, 21, 30, tzinfo=timezone(timedelta(hours=8)))}
    )
    first.append_event(run.id, non_utc_envelope)
    first.archive_receipt(
        run.id,
        receipt_key="restart:receipt",
        source="agentteams",
        kind="project-response",
        payload={"project_id": run.agentteams_project_id, "ok": True},
    )

    restarted = PostgresBridgeStore(postgres_url)
    loaded = restarted.get_run(run.id)
    assert loaded.version == updated.version
    assert loaded.checkpoint["restart_marker"] == "persisted"
    assert [item.id for item in restarted.active_runs()] == [run.id]
    assert restarted.events(run.id)["chain_valid"] is True
    assert restarted.receipts(run.id)["chain_valid"] is True

    with psycopg.connect(postgres_url) as connection:
        migration_count, task_graph_type, checkpoint_type = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM bridge_schema_migrations),
              pg_typeof(task_graph)::text,
              pg_typeof(checkpoint)::text
            FROM bridge_runs WHERE id=%s
            """,
            (run.id,),
        ).fetchone()
    assert migration_count == 2
    assert (task_graph_type, checkpoint_type) == ("jsonb", "jsonb")


def test_concurrent_bridge_store_initialization_replays_one_migration(
    postgres_url: str,
) -> None:
    def initialize(_index: int) -> bool:
        return PostgresBridgeStore(postgres_url).ping()

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert all(executor.map(initialize, range(8)))

    with psycopg.connect(postgres_url) as connection:
        rows = connection.execute(
            "SELECT version FROM bridge_schema_migrations ORDER BY version"
        ).fetchall()
    assert rows == [
        ("001_bridge_control_plane.sql",),
        ("002_campaign_safety_attention.sql",),
    ]


def test_bridge_run_optimistic_update_allows_one_concurrent_winner(postgres_url: str) -> None:
    stores = [PostgresBridgeStore(postgres_url) for _ in range(2)]
    run = stores[0].create_run(_run())
    barrier = Barrier(2)

    def update(item: Tuple[int, PostgresBridgeStore]) -> str:
        index, store = item
        candidate = run.model_copy(update={"checkpoint": {"winner": index}})
        barrier.wait()
        try:
            store.update_run(candidate, expected_version=run.version)
            return "updated"
        except BridgeError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(update, enumerate(stores)))

    assert sorted(outcomes) == ["run_version_conflict", "updated"]
    assert stores[0].get_run(run.id).version == 2


def test_concurrent_event_writers_form_one_database_serialized_chain(postgres_url: str) -> None:
    writer_count = 8
    stores = [PostgresBridgeStore(postgres_url) for _ in range(writer_count)]
    run = stores[0].create_run(_run())
    barrier = Barrier(writer_count)

    def append(item: Tuple[int, PostgresBridgeStore]) -> Dict[str, Any]:
        index, store = item
        barrier.wait()
        return store.append_event(run.id, _envelope(run, index))

    with ThreadPoolExecutor(max_workers=writer_count) as executor:
        results = list(executor.map(append, enumerate(stores)))

    assert sorted(item["sequence"] for item in results) == list(range(1, writer_count + 1))
    persisted = stores[0].events(run.id)
    assert persisted["total"] == writer_count
    assert persisted["chain_valid"] is True


def test_bridge_event_rejects_naive_timestamp(postgres_url: str) -> None:
    store = PostgresBridgeStore(postgres_url)
    run = store.create_run(_run())
    naive = _envelope(run, 1).model_copy(update={"created_at": datetime(2026, 8, 29, 21, 30)})
    with pytest.raises(BridgeError) as raised:
        store.append_event(run.id, naive)
    assert raised.value.code == "event_time_invalid"
    assert store.events(run.id)["total"] == 0


def test_bridge_ledgers_require_the_current_operation_lease_owner(
    postgres_url: str,
) -> None:
    store = PostgresBridgeStore(postgres_url)
    run = store.create_run(_run())
    claimed = store.claim_operation(
        run.id,
        "ledger-fence",
        "owner-a",
        timeout_seconds=30,
    )

    for owner in (None, "owner-b"):
        with pytest.raises(BridgeError) as event_error:
            store.append_event(run.id, _envelope(run, 1), lease_owner=owner)
        assert event_error.value.code == "operation_lease_lost"
        with pytest.raises(BridgeError) as receipt_error:
            store.archive_receipt(
                run.id,
                receipt_key="lease-fence:%s" % (owner or "missing"),
                source="test",
                kind="lease-fence",
                payload={"owner": owner},
                lease_owner=owner,
            )
        assert receipt_error.value.code == "operation_lease_lost"

    renewed = store.renew_operation(
        run.id,
        "owner-a",
        timeout_seconds=30,
    )
    assert renewed.checkpoint["_operation_lease"]["owner_id"] == "owner-a"
    event = store.append_event(
        run.id,
        _envelope(claimed, 2),
        lease_owner="owner-a",
    )
    receipt = store.archive_receipt(
        run.id,
        receipt_key="lease-fence:owner-a",
        source="test",
        kind="lease-fence",
        payload={"owner": "owner-a"},
        lease_owner="owner-a",
    )
    assert event["sequence"] == 1
    assert receipt["sequence"] == 1
    store.release_operation(run.id, "owner-a")


def test_concurrent_receipt_replay_is_idempotent_and_conflicts_fail_closed(
    postgres_url: str,
) -> None:
    writer_count = 8
    stores = [PostgresBridgeStore(postgres_url) for _ in range(writer_count)]
    run = stores[0].create_run(_run())
    same_barrier = Barrier(writer_count)
    payload = {"event_id": "$same", "body": {"status": "ok"}}

    def archive_same(store: PostgresBridgeStore) -> Dict[str, Any]:
        same_barrier.wait()
        return store.archive_receipt(
            run.id,
            receipt_key="matrix:same",
            source="matrix",
            kind="raw-message",
            payload=payload,
        )

    with ThreadPoolExecutor(max_workers=writer_count) as executor:
        same_results = list(executor.map(archive_same, stores))

    assert len({item["receipt_hash"] for item in same_results}) == 1
    assert sum(not item["idempotent_replay"] for item in same_results) == 1

    conflict_barrier = Barrier(2)

    def archive_conflict(item: Tuple[int, PostgresBridgeStore]) -> str:
        index, store = item
        conflict_barrier.wait()
        try:
            store.archive_receipt(
                run.id,
                receipt_key="matrix:conflict",
                source="matrix",
                kind="raw-message",
                payload={"winner_candidate": index},
            )
            return "inserted"
        except BridgeError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        conflict_results = list(executor.map(archive_conflict, enumerate(stores[:2])))

    assert sorted(conflict_results) == ["inserted", "receipt_key_conflict"]
    persisted = stores[0].receipts(run.id)
    assert persisted["total"] == 2
    assert persisted["chain_valid"] is True


def test_database_triggers_reject_event_and_receipt_update_delete_and_truncate(
    postgres_url: str,
) -> None:
    store = PostgresBridgeStore(postgres_url)
    run = store.create_run(_run())
    store.append_event(run.id, _envelope(run, 1))
    store.archive_receipt(
        run.id,
        receipt_key="immutable:receipt",
        source="agentteams",
        kind="project-response",
        payload={"ok": True},
    )

    statements = (
        "UPDATE bridge_events SET kind='tampered' WHERE run_id=%s",
        "DELETE FROM bridge_events WHERE run_id=%s",
        "TRUNCATE bridge_events",
        "UPDATE bridge_receipts SET kind='tampered' WHERE run_id=%s",
        "DELETE FROM bridge_receipts WHERE run_id=%s",
        "TRUNCATE bridge_receipts",
    )
    for statement in statements:
        parameters = () if statement.startswith("TRUNCATE") else (run.id,)
        with psycopg.connect(postgres_url, autocommit=True) as connection:
            try:
                connection.execute(statement, parameters)
            except psycopg.Error as error:
                assert error.sqlstate == "23000"
            else:
                raise AssertionError("append-only trigger accepted: %s" % statement)

    assert store.events(run.id)["total"] == 1
    assert store.events(run.id)["chain_valid"] is True
    assert store.receipts(run.id)["total"] == 1
    assert store.receipts(run.id)["chain_valid"] is True

    with psycopg.connect(postgres_url, autocommit=True) as connection:
        connection.execute(
            "ALTER TABLE bridge_receipts DISABLE TRIGGER bridge_receipts_no_update_or_delete"
        )
        connection.execute(
            "UPDATE bridge_receipts SET payload=%s::jsonb WHERE run_id=%s",
            ('{"tampered":true}', run.id),
        )
        connection.execute(
            "ALTER TABLE bridge_receipts ENABLE TRIGGER bridge_receipts_no_update_or_delete"
        )
    assert store.receipts(run.id)["chain_valid"] is False


def test_bridge_migration_checksum_drift_fails_closed(postgres_url: str) -> None:
    PostgresBridgeStore(postgres_url)
    with psycopg.connect(postgres_url) as connection:
        connection.execute(
            """
            UPDATE bridge_schema_migrations SET sha256=%s
             WHERE version='001_bridge_control_plane.sql'
            """,
            ("0" * 64,),
        )
    try:
        PostgresBridgeStore(postgres_url)
    except RuntimeError as error:
        assert "migration checksum differs" in str(error)
    else:
        raise AssertionError("bridge migration checksum drift was accepted")


def test_bridge_runtime_role_cannot_mutate_ledgers_or_disable_triggers(
    postgres_url: str,
) -> None:
    store = PostgresBridgeStore(postgres_url)
    run = store.create_run(_run())
    store.append_event(run.id, _envelope(run, 1))
    security_sql = (
        Path(__file__).resolve().parents[2] / "deploy/postgres/agentteams_bridge_security.sql"
    ).read_text(encoding="utf-8")
    with psycopg.connect(postgres_url) as connection:
        connection.execute(security_sql)
        privileges = connection.execute(
            """
            SELECT
              has_table_privilege('egoagentos_bridge_runtime', 'bridge_events', 'SELECT'),
              has_table_privilege('egoagentos_bridge_runtime', 'bridge_events', 'INSERT'),
              has_table_privilege('egoagentos_bridge_runtime', 'bridge_events', 'UPDATE'),
              has_table_privilege('egoagentos_bridge_runtime', 'bridge_events', 'DELETE'),
              has_table_privilege('egoagentos_bridge_runtime', 'bridge_events', 'TRUNCATE'),
              has_column_privilege(
                  'egoagentos_bridge_runtime', 'bridge_runs', 'checkpoint', 'UPDATE'
              ),
              has_column_privilege(
                  'egoagentos_bridge_runtime', 'bridge_runs', 'objective', 'UPDATE'
              )
            """
        ).fetchone()
    assert privileges == (True, True, False, False, False, True, False)

    forbidden = (
        "UPDATE bridge_events SET kind='tampered' WHERE run_id=%s",
        "DELETE FROM bridge_events WHERE run_id=%s",
        "TRUNCATE bridge_events",
        "ALTER TABLE bridge_events DISABLE TRIGGER bridge_events_no_update_or_delete",
    )
    for statement in forbidden:
        parameters = () if "%s" not in statement else (run.id,)
        with psycopg.connect(postgres_url, autocommit=True) as connection:
            connection.execute("SET ROLE egoagentos_bridge_runtime")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement, parameters)

    login_role = "egoagentos_bridge_login_test"
    login_password = "b" * 64
    with psycopg.connect(postgres_url, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=%s", (login_role,)
        ).fetchone()
        if exists is None:
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(login_role), sql.Literal(login_password)
                )
            )
        connection.execute(
            sql.SQL("GRANT egoagentos_bridge_runtime TO {}").format(sql.Identifier(login_role))
        )

    runtime_url = _login_url(postgres_url, login_role, login_password)
    runtime_store = PostgresBridgeStore(runtime_url, migration_mode="verify")
    with psycopg.connect(runtime_url) as connection:
        state = connection.execute(
            """
            SELECT current_user, session_user, rolsuper, rolcreatedb, rolcreaterole,
                   rolreplication, rolbypassrls,
                   has_schema_privilege(current_user, 'public', 'CREATE')
              FROM pg_roles WHERE rolname=current_user
            """
        ).fetchone()
        assert state == (
            login_role,
            login_role,
            False,
            False,
            False,
            False,
            False,
            False,
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("CREATE TABLE bridge_runtime_must_not_create(id integer)")
    assert runtime_store.get_run(run.id).id == run.id
    runtime_store.append_event(run.id, _envelope(run, 2))
    runtime_store.archive_receipt(
        run.id,
        receipt_key="runtime-role:receipt",
        source="agentteams",
        kind="project-response",
        payload={"runtime_role": True},
    )
    assert runtime_store.events(run.id)["chain_valid"] is True
    assert runtime_store.receipts(run.id)["chain_valid"] is True


def test_postgres_campaign_extension_authority_restarts_with_sqlite_parity(
    postgres_url: str,
) -> None:
    first = PostgresBridgeStore(postgres_url)
    run = first.create_run(_extension_run())
    _populate_complete_authority(first, run)

    before = first.replay_extension_authority(
        run.id,
        project_id=run.agentteams_project_id,
        configuration_id="D",
    )
    restarted = PostgresBridgeStore(postgres_url)
    after = restarted.replay_extension_authority(
        run.id,
        project_id=run.agentteams_project_id,
        configuration_id="D",
    )

    assert after == before
    assert after["events"]["total"] == 7
    assert after["events"]["chain_valid"] is True
    assert len(after["task_leases"]) == 1
    assert len(after["evaluator_bindings"]) == 1
    assert len(after["guardian_decisions"]) == 1
    assert len(after["safety_decisions"]) == 1
    assert after["projection"]["event_type"] == "USER_STATUS_PROJECTION"

    for project_id, configuration_id in (
        ("another-project", "D"),
        (run.agentteams_project_id, "E"),
    ):
        with pytest.raises(BridgeError) as mismatch:
            restarted.replay_extension_authority(
                run.id,
                project_id=project_id,
                configuration_id=configuration_id,
            )
        assert mismatch.value.code == "campaign_binding_not_found"


def test_concurrent_postgres_extension_replay_uses_one_per_run_chain(
    postgres_url: str,
) -> None:
    writer_count = 8
    stores = [PostgresBridgeStore(postgres_url) for _ in range(writer_count)]
    run = stores[0].create_run(_extension_run())
    stores[0].bind_campaign(run.id, _binding())
    barrier = Barrier(writer_count)

    def append(store: PostgresBridgeStore) -> Dict[str, Any]:
        barrier.wait()
        return store.append_extension_event(
            run.id,
            event_type="SYSTEM_RISK_ASSESSMENT",
            event=_system_high(),
            idempotency_key="risk:concurrent",
            memory_watermark=7,
        )

    with ThreadPoolExecutor(max_workers=writer_count) as executor:
        results = list(executor.map(append, stores))

    assert len({item["event_hash"] for item in results}) == 1
    assert sum(not item["idempotent_replay"] for item in results) == 1
    replay = stores[0].extension_events(
        run.id,
        project_id=run.agentteams_project_id,
        configuration_id="D",
    )
    assert replay["total"] == 1
    assert replay["chain_valid"] is True


def test_postgres_extension_history_rejects_update_delete_and_truncate(
    postgres_url: str,
) -> None:
    store = PostgresBridgeStore(postgres_url)
    run = store.create_run(_extension_run())
    _populate_complete_authority(store, run)

    statements = (
        "UPDATE bridge_runs SET campaign_id='changed' WHERE id=%s",
        "UPDATE bridge_extension_events SET event_type='changed' WHERE run_id=%s",
        "DELETE FROM bridge_extension_events WHERE run_id=%s",
        "TRUNCATE bridge_extension_events CASCADE",
        "UPDATE bridge_task_leases SET key_id='changed' WHERE run_id=%s",
        "DELETE FROM bridge_task_leases WHERE run_id=%s",
        "TRUNCATE bridge_task_leases CASCADE",
        "UPDATE bridge_evaluator_bindings SET key_id='changed' WHERE run_id=%s",
        "DELETE FROM bridge_evaluator_bindings WHERE run_id=%s",
        "TRUNCATE bridge_evaluator_bindings CASCADE",
    )
    for statement in statements:
        parameters = () if statement.startswith("TRUNCATE") else (run.id,)
        with psycopg.connect(postgres_url, autocommit=True) as connection:
            with pytest.raises(psycopg.Error) as raised:
                connection.execute(statement, parameters)
        assert raised.value.sqlstate == "23000"
