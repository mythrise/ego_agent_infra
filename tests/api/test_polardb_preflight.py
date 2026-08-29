import json
from pathlib import Path

import pytest

import apps.api.polardb_preflight as preflight_module

from apps.api.polardb_preflight import (
    DISPOSABLE_MARKER,
    EXPECTED_RUNTIME_UPDATE_COLUMNS,
    EXPECTED_TRIGGER_FUNCTIONS,
    EXPECTED_TABLES,
    EXPECTED_TRIGGERS,
    PRIVILEGE_TABLES,
    ManifestError,
    PostgresInspector,
    SafetyGateError,
    _packaged_migrations,
    load_manifest,
    main,
    run_fresh_schema_replay,
    run_preflight,
    verify_destructive_gate,
)


ROOT = Path(__file__).resolve().parents[2]
WRITER_URL = "postgresql://writer:super-secret@writer.example:5432/egoagentos_acceptance_ci"
READER_URL = "postgresql://reader:other-secret@reader.example:5432/egoagentos_acceptance_ci"


def manifest() -> dict:
    return {
        "schema_version": "egoagentos.polardb-live-acceptance/v1",
        "target": {
            "environment": "nonproduction",
            "expected_database": "egoagentos_acceptance_ci",
            "writer_url_env": "TEST_POLARDB_WRITER_URL",
            "reader_url_env": "TEST_POLARDB_READER_URL",
            "runtime_url_env": None,
            "auditor_url_env": None,
            "minimum_server_version_num": 120000,
            "require_tls": True,
            "require_polardb_marker": True,
            "require_read_endpoint": True,
            "require_rls": True,
            "require_force_rls": False,
            "require_role_logins": False,
            "disposable_database_prefix": "egoagentos_acceptance_",
            "disposable_database_marker": DISPOSABLE_MARKER,
            "roles": {
                "runtime": "egoagentos_runtime",
                "auditor": "egoagentos_auditor",
                "evidence_writer": "egoagentos_evidence_writer",
                "memory_curator": "egoagentos_memory_curator",
            },
            "pgvector": "optional",
        },
        "operations": {
            "fresh_schema_replay": {"authorized": False, "status": "NOT_RUN"},
            "pitr_restore": {"authorized": False, "status": "NOT_RUN"},
            "multi_az_failover": {"authorized": False, "status": "NOT_RUN"},
        },
        "truth_boundary": {},
    }


def endpoint(label: str, *, polar: bool = True) -> dict:
    reader = label == "reader"
    return {
        "label": label,
        "location": "%s.example:5432/egoagentos_acceptance_ci" % label,
        "identity": {
            "advertised_version": "PolarDB for PostgreSQL 14" if polar else "PostgreSQL 16",
            "server_version": "14.11",
            "server_version_num": 140011,
            "endpoint_read_only": reader,
            "in_recovery": reader,
            "database_name": "egoagentos_acceptance_ci",
            "current_user": "acceptance_owner",
            "server_address": "10.0.0.10" if not reader else "10.0.0.11",
            "server_port": 5432,
            "polar_node_type": "reader" if reader and polar else ("writer" if polar else None),
        },
        "tls": {"ssl": True, "tls_version": "TLSv1.3", "cipher": "TEST-CIPHER"},
        "jsonb_supported": True,
        "polar_marker_observed": polar,
        "polar_setting_names": ["polar_node_type"] if polar else [],
        "pgvector": None,
        "database_comment": DISPOSABLE_MARKER,
        "session_forced_read_only_after_identity": True,
    }


def privileges() -> dict:
    result: dict = {}
    roles = (
        "egoagentos_runtime",
        "egoagentos_auditor",
        "egoagentos_evidence_writer",
        "egoagentos_memory_curator",
    )
    evidence_read = {"tasks", "approvals", "evidence"}
    curator_read = {"tasks", "evidence", "memory_candidates", "memories"}
    for role in roles:
        result[role] = {}
        for table in PRIVILEGE_TABLES:
            if role == "egoagentos_runtime":
                result[role][table] = {
                    "select": True,
                    "insert": table not in {"schema_migrations"},
                    "update": False,
                    "delete": False,
                    "update_columns": {
                        column: True for column in EXPECTED_RUNTIME_UPDATE_COLUMNS.get(table, ())
                    },
                }
            elif role == "egoagentos_auditor":
                result[role][table] = {
                    "select": True,
                    "insert": False,
                    "update": False,
                    "delete": False,
                    "update_columns": {},
                }
            elif role == "egoagentos_evidence_writer":
                result[role][table] = {
                    "select": table in evidence_read,
                    "insert": table == "evidence",
                    "update": False,
                    "delete": False,
                    "update_columns": {},
                }
            else:
                result[role][table] = {
                    "select": table in curator_read,
                    "insert": table == "memory_candidates",
                    "update": False,
                    "delete": False,
                    "update_columns": {},
                }
    return result


class FakeInspector:
    def __init__(
        self,
        *,
        polar: bool = True,
        migration_matches: bool = True,
        login_database: str = "egoagentos_acceptance_ci",
        login_is_member: bool = True,
        capability_group_nologin: bool = True,
        triggers_enabled: bool = True,
    ) -> None:
        self.polar = polar
        self.migration_matches = migration_matches
        self.login_database = login_database
        self.login_is_member = login_is_member
        self.capability_group_nologin = capability_group_nologin
        self.triggers_enabled = triggers_enabled
        self.notify_calls = 0
        self.topology_calls = []
        self.fresh_calls = 0
        self.target_reads = 0

    def inspect_endpoint(self, _url: str, label: str) -> dict:
        return endpoint(label, polar=self.polar)

    def inspect_control_plane(self, _url: str, roles: dict[str, str]) -> dict:
        assert roles == {
            "runtime": "egoagentos_runtime",
            "auditor": "egoagentos_auditor",
            "evidence_writer": "egoagentos_evidence_writer",
            "memory_curator": "egoagentos_memory_curator",
        }
        return {
            "tables": [
                {"table_name": table, "relrowsecurity": True, "relforcerowsecurity": False}
                for table in EXPECTED_TABLES
            ],
            "triggers": [
                {
                    "trigger_name": trigger,
                    "trigger_enabled": "O" if self.triggers_enabled else "D",
                    "function_name": EXPECTED_TRIGGER_FUNCTIONS[trigger],
                }
                for trigger in EXPECTED_TRIGGERS
            ],
            "policies": [
                {
                    "tablename": table,
                    "policyname": "%s_tenant_policy" % table,
                    "cmd": "ALL",
                    "qual": "(tenant_id = egoagentos_current_tenant())",
                    "with_check": "(tenant_id = egoagentos_current_tenant())",
                }
                for table in EXPECTED_TABLES
            ],
            "migrations": [
                {
                    "version": version,
                    "sha256": digest if self.migration_matches else "a" * 64,
                }
                for version, digest in _packaged_migrations().items()
            ],
            "roles": list(roles.values()),
            "privileges": privileges(),
        }

    def inspect_login(self, url: str, expected_capability_group: str) -> dict:
        login_identity = "%s_login" % expected_capability_group
        return {
            "login_identity": login_identity,
            "current_user": login_identity,
            "database_name": self.login_database,
            "login_can_login": True,
            "dedicated_login": True,
            "capability_group": expected_capability_group,
            "capability_group_nologin": self.capability_group_nologin,
            "capability_group_member": self.login_is_member,
            "tls": True,
        }

    def active_notify(self, _url: str) -> dict:
        self.notify_calls += 1
        return {"matched": True, "received": 1, "channel": "ego_polardb_preflight"}

    def active_topology(self, url: str) -> dict:
        self.topology_calls.append(url)
        return {
            "temporary_write_accepted": "writer.example" in url,
            "error_type": None if "writer.example" in url else "ReadOnlySqlTransaction",
            "rolled_back": True,
        }

    def disposable_target(self, _url: str) -> dict:
        self.target_reads += 1
        return {
            "database_name": "egoagentos_acceptance_ci",
            "database_comment": DISPOSABLE_MARKER,
            "location": "writer.example:5432/egoagentos_acceptance_ci",
            "tls": True,
        }

    def fresh_schema_replay(
        self,
        _url: str,
        *,
        expected_database: str,
        expected_marker: str,
        require_tls: bool,
    ) -> dict:
        assert expected_database == "egoagentos_acceptance_ci"
        assert expected_marker == DISPOSABLE_MARKER
        assert require_tls is True
        self.fresh_calls += 1
        return {
            "migrations": [{"version": "001_control_plane.sql", "sha256": "a" * 64}],
            "tables": list(EXPECTED_TABLES),
            "security_roles_reapply_required": True,
            "target_reverified_in_destructive_transaction": True,
        }


def environment() -> dict:
    return {
        "TEST_POLARDB_WRITER_URL": WRITER_URL,
        "TEST_POLARDB_READER_URL": READER_URL,
    }


def test_example_manifest_validates_without_a_database() -> None:
    value = load_manifest(ROOT / "deploy/polardb/acceptance-manifest.example.json")
    assert value["target"]["require_polardb_marker"] is True
    assert value["operations"]["pitr_restore"]["status"] == "NOT_RUN"


def test_read_only_production_manifest_does_not_require_a_disposable_database_name(
    tmp_path: Path,
) -> None:
    value = manifest()
    value["target"]["environment"] = "production"
    value["target"]["expected_database"] = "egoagentos_prod"
    path = tmp_path / "production.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert load_manifest(path)["target"]["expected_database"] == "egoagentos_prod"


def test_default_preflight_is_read_only_redacted_and_machine_readable() -> None:
    inspector = FakeInspector()
    report = run_preflight(manifest(), environment(), inspector=inspector)
    encoded = json.dumps(report, sort_keys=True)
    assert report["mode"] == "read_only"
    assert report["summary"]["status"] == "PASS_WITH_GAPS"
    assert report["checks"]["writer"]["tls"]["status"] == "PASS"
    assert report["checks"]["reader"]["endpoint_role"]["status"] == "PASS"
    assert report["checks"]["control_plane"]["role_privileges"]["status"] == "PASS"
    assert report["checks"]["control_plane"]["role_privileges"]["evidence"][
        "runtime_update_columns"
    ]["tasks"] == sorted(EXPECTED_RUNTIME_UPDATE_COLUMNS["tasks"])
    assert report["checks"]["active_notify"]["status"] == "SKIP"
    assert report["checks"]["active_topology"]["status"] == "SKIP"
    assert inspector.notify_calls == 0
    assert inspector.topology_calls == []
    assert "super-secret" not in encoded
    assert "other-secret" not in encoded


def test_runtime_column_update_overgrant_is_a_required_failure() -> None:
    class OvergrantInspector(FakeInspector):
        def inspect_control_plane(self, url: str, roles: dict[str, str]) -> dict:
            catalog = super().inspect_control_plane(url, roles)
            catalog["privileges"]["egoagentos_runtime"]["tasks"]["update_columns"]["tenant_id"] = (
                True
            )
            return catalog

    report = run_preflight(manifest(), environment(), inspector=OvergrantInspector())
    role_check = report["checks"]["control_plane"]["role_privileges"]
    assert role_check["status"] == "FAIL"
    assert any("tenant_id" in failure for failure in role_check["evidence"]["failures"])
    assert report["summary"]["status"] == "FAIL"


def test_required_polardb_marker_fails_on_generic_postgres() -> None:
    report = run_preflight(manifest(), environment(), inspector=FakeInspector(polar=False))
    assert report["checks"]["writer"]["polardb_identity"]["status"] == "FAIL"
    assert report["summary"]["status"] == "FAIL"


def test_packaged_migration_digest_mismatch_is_a_required_failure() -> None:
    report = run_preflight(
        manifest(), environment(), inspector=FakeInspector(migration_matches=False)
    )
    schema = report["checks"]["control_plane"]["schema"]
    assert schema["status"] == "FAIL"
    assert schema["evidence"]["mismatched_migrations"] == sorted(_packaged_migrations())
    assert report["summary"]["status"] == "FAIL"


def test_disabled_audit_trigger_is_a_required_failure() -> None:
    report = run_preflight(
        manifest(), environment(), inspector=FakeInspector(triggers_enabled=False)
    )
    trigger_check = report["checks"]["control_plane"]["audit_triggers"]
    assert trigger_check["status"] == "FAIL"
    assert trigger_check["evidence"]["invalid_triggers"] == sorted(EXPECTED_TRIGGERS)
    assert report["summary"]["status"] == "FAIL"


def test_dedicated_role_login_must_target_the_expected_database() -> None:
    value = manifest()
    value["target"].update(
        {
            "runtime_url_env": "TEST_POLARDB_RUNTIME_URL",
            "auditor_url_env": "TEST_POLARDB_AUDITOR_URL",
            "require_role_logins": True,
        }
    )
    urls = {
        **environment(),
        "TEST_POLARDB_RUNTIME_URL": "postgresql://runtime:secret@writer.example/wrong",
        "TEST_POLARDB_AUDITOR_URL": "postgresql://auditor:secret@writer.example/wrong",
    }
    report = run_preflight(
        value,
        urls,
        inspector=FakeInspector(login_database="wrong_database"),
    )
    assert report["checks"]["runtime_login"]["status"] == "FAIL"
    assert report["checks"]["auditor_login"]["status"] == "FAIL"
    assert report["summary"]["status"] == "FAIL"


def test_dedicated_login_must_be_member_of_nologin_capability_group() -> None:
    value = manifest()
    value["target"].update(
        {
            "runtime_url_env": "TEST_POLARDB_RUNTIME_URL",
            "require_role_logins": True,
        }
    )
    urls = {
        **environment(),
        "TEST_POLARDB_RUNTIME_URL": "postgresql://runtime-login:secret@writer.example/db",
    }

    report = run_preflight(
        value,
        urls,
        inspector=FakeInspector(login_is_member=False),
    )

    login = report["checks"]["runtime_login"]
    assert login["status"] == "FAIL"
    assert login["evidence"]["login_identity"] == "egoagentos_runtime_login"
    assert login["evidence"]["expected_role"] == "egoagentos_runtime"
    assert login["evidence"]["capability_group_member"] is False
    assert report["summary"]["status"] == "FAIL"


def test_dedicated_login_accepts_distinct_member_of_nologin_capability_group() -> None:
    value = manifest()
    value["target"].update(
        {
            "runtime_url_env": "TEST_POLARDB_RUNTIME_URL",
            "require_role_logins": True,
        }
    )
    urls = {
        **environment(),
        "TEST_POLARDB_RUNTIME_URL": "postgresql://runtime-login:secret@writer.example/db",
    }

    report = run_preflight(value, urls, inspector=FakeInspector())

    login = report["checks"]["runtime_login"]
    assert login["status"] == "PASS"
    assert login["evidence"]["login_identity"] != login["evidence"]["expected_role"]
    assert login["evidence"]["capability_group_nologin"] is True
    assert login["evidence"]["capability_group_member"] is True


def test_active_notify_and_topology_require_flags() -> None:
    inspector = FakeInspector()
    report = run_preflight(
        manifest(),
        environment(),
        inspector=inspector,
        active_notify=True,
        active_topology=True,
    )
    assert report["mode"] == "explicit_transient_probe"
    assert report["checks"]["active_notify"]["status"] == "PASS"
    assert report["checks"]["active_topology"]["writer"]["status"] == "PASS"
    assert report["checks"]["active_topology"]["reader"]["status"] == "PASS"
    assert inspector.notify_calls == 1
    assert inspector.topology_calls == [WRITER_URL, READER_URL]


def test_production_read_only_probe_requires_separate_override() -> None:
    value = manifest()
    value["target"]["environment"] = "production"
    with pytest.raises(ManifestError, match="allow-production-readonly"):
        run_preflight(value, environment(), inspector=FakeInspector())
    report = run_preflight(
        value,
        environment(),
        inspector=FakeInspector(),
        allow_production_readonly=True,
    )
    assert report["target"]["environment"] == "production"
    with pytest.raises(ManifestError, match="only permit the read-only"):
        run_preflight(
            value,
            environment(),
            inspector=FakeInspector(),
            active_notify=True,
            allow_production_readonly=True,
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda value: None, "allow-destructive"),
        (
            lambda value: value["operations"]["fresh_schema_replay"].update(authorized=True),
            "confirm-database",
        ),
    ],
)
def test_destructive_gate_fails_closed(change, message: str) -> None:
    value = manifest()
    change(value)
    with pytest.raises(SafetyGateError, match=message):
        verify_destructive_gate(
            "fresh_schema_replay",
            value,
            {
                "database_name": "egoagentos_acceptance_ci",
                "database_comment": DISPOSABLE_MARKER,
                "tls": True,
            },
            allow_destructive=value["operations"]["fresh_schema_replay"]["authorized"],
            confirm_database=None,
            confirm_marker=None,
        )


def test_fresh_schema_and_future_pitr_share_the_same_redundant_gate() -> None:
    value = manifest()
    target = {
        "database_name": "egoagentos_acceptance_ci",
        "database_comment": DISPOSABLE_MARKER,
        "tls": True,
    }
    for operation in ("fresh_schema_replay", "pitr_restore"):
        value["operations"][operation]["authorized"] = True
        result = verify_destructive_gate(
            operation,
            value,
            target,
            allow_destructive=True,
            confirm_database="egoagentos_acceptance_ci",
            confirm_marker=DISPOSABLE_MARKER,
        )
        assert result["authorized"] is True


def test_programmatic_destructive_gate_rejects_weakened_prefix() -> None:
    value = manifest()
    value["operations"]["fresh_schema_replay"]["authorized"] = True
    value["target"]["disposable_database_prefix"] = ""
    with pytest.raises(SafetyGateError, match="safe disposable"):
        verify_destructive_gate(
            "fresh_schema_replay",
            value,
            {
                "database_name": "egoagentos_acceptance_ci",
                "database_comment": DISPOSABLE_MARKER,
                "tls": True,
            },
            allow_destructive=True,
            confirm_database="egoagentos_acceptance_ci",
            confirm_marker=DISPOSABLE_MARKER,
        )


def test_fresh_schema_replay_reads_marker_twice_before_execution() -> None:
    value = manifest()
    value["operations"]["fresh_schema_replay"]["authorized"] = True
    inspector = FakeInspector()
    report = run_fresh_schema_replay(
        value,
        environment(),
        allow_destructive=True,
        confirm_database="egoagentos_acceptance_ci",
        confirm_marker=DISPOSABLE_MARKER,
        inspector=inspector,
    )
    assert report["mode"] == "DESTRUCTIVE_FRESH_SCHEMA_REPLAY"
    assert report["summary"]["status"] == "PASS"
    assert report["truth_boundary"]["pitr_restore"] == "NOT_RUN"
    assert inspector.target_reads == 2
    assert inspector.fresh_calls == 1
    assert report["result"]["target_reverified_in_destructive_transaction"] is True


def test_fresh_schema_replay_explicitly_applies_migrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        def __init__(self, *, one=None, all_rows=None) -> None:
            self.one = one
            self.all_rows = all_rows or []

        def fetchone(self):
            return self.one

        def fetchall(self):
            return self.all_rows

    class Connection:
        def __init__(self, verification: bool = False) -> None:
            self.verification = verification

        def execute(self, statement, _parameters=None):
            text = str(statement)
            if "FROM pg_database" in text:
                return Result(
                    one={
                        "database_name": "egoagentos_acceptance_ci",
                        "database_comment": DISPOSABLE_MARKER,
                        "tls": True,
                    }
                )
            if "FROM schema_migrations" in text:
                return Result(
                    all_rows=[{"version": "001_control_plane.sql", "sha256": "a" * 64}]
                )
            if "FROM pg_tables" in text:
                return Result(all_rows=[{"tablename": "tasks"}])
            return Result()

        def close(self) -> None:
            return None

    connections = [Connection(), Connection(verification=True)]
    inspector = PostgresInspector()
    monkeypatch.setattr(inspector, "_connect", lambda _url: connections.pop(0))
    store_calls = []

    def fake_store(database_url: str, *, migration_mode: str):
        store_calls.append((database_url, migration_mode))
        return object()

    monkeypatch.setattr(preflight_module, "PostgresStore", fake_store)

    result = inspector.fresh_schema_replay(
        "postgresql://owner:redacted@writer.example/egoagentos_acceptance_ci",
        expected_database="egoagentos_acceptance_ci",
        expected_marker=DISPOSABLE_MARKER,
        require_tls=True,
    )

    assert store_calls == [
        (
            "postgresql://owner:redacted@writer.example/egoagentos_acceptance_ci",
            "apply",
        )
    ]
    assert result["tables"] == ["tasks"]


def test_cli_missing_secret_emits_json_without_connecting(tmp_path: Path) -> None:
    value = manifest()
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "report.json"
    manifest_path.write_text(json.dumps(value), encoding="utf-8")
    exit_code = main(
        [
            "preflight",
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
        ]
    )
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["summary"]["status"] == "ERROR"
    assert report["truth_boundary"]["cloud_resources_created"] is False


def test_manifest_rejects_embedded_url_field_name(tmp_path: Path) -> None:
    value = manifest()
    value["target"]["writer_url_env"] = WRITER_URL
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ManifestError, match="environment variable"):
        load_manifest(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("disposable_database_prefix", "", "safe prefix"),
        ("disposable_database_marker", "operator-chosen", "must be EGOAGENTOS"),
    ],
)
def test_manifest_rejects_weakened_disposable_target_gate(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    unsafe = manifest()
    unsafe["target"][field] = value
    path = tmp_path / "unsafe-gate.json"
    path.write_text(json.dumps(unsafe), encoding="utf-8")
    with pytest.raises(ManifestError, match=message):
        load_manifest(path)
