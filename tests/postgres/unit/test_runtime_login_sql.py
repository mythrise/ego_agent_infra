from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
LOGIN_SQL = ROOT / "deploy/postgres/configure_runtime_login.sql"
LOGIN_SH = ROOT / "deploy/postgres/configure_runtime_login.sh"


def test_runtime_login_source_clears_every_direct_acl_surface_before_membership() -> None:
    source = LOGIN_SQL.read_text(encoding="utf-8")
    membership_offset = source.index("-- Remove stale memberships")
    required_revokes = (
        "REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I",
        "REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I",
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %I",
        "REVOKE %s (%I) ON TABLE %I.%I FROM %I",
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM %I",
        "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM %I",
    )

    for statement in required_revokes:
        assert statement in source
        assert source.index(statement) < membership_offset

    assert source.count("CROSS JOIN LATERAL aclexplode(") >= 6
    assert "has_database_privilege(role_state.rolname, current_database(), 'CONNECT')" in source
    assert "AND NOT group_state.rolcanlogin" in source


def test_runtime_login_shell_applies_group_security_before_login_hardening() -> None:
    source = LOGIN_SH.read_text(encoding="utf-8")
    security_offset = source.index('--file="$EGO_SECURITY_SQL"')
    hardening_offset = source.index("--file=/opt/egoagentos-postgres/configure_runtime_login.sql")

    assert security_offset < hardening_offset
    assert source.count("--set=ON_ERROR_STOP=1") == 2
