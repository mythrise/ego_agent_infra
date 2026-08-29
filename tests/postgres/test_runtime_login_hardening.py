from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from apps.agentteams_bridge.postgres_store import PostgresBridgeStore
from apps.api.postgres_store import PostgresStore


ROOT = Path(__file__).resolve().parents[2]
LOGIN_SQL = ROOT / "deploy/postgres/configure_runtime_login.sql"


def _login_url(postgres_url: str, user: str, password: str) -> str:
    parsed = urlsplit(postgres_url)
    host = parsed.hostname or "127.0.0.1"
    port = ":%d" % parsed.port if parsed.port else ""
    netloc = "%s:%s@%s%s" % (quote(user), quote(password), host, port)
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _execute_runtime_login_sql(
    connection: psycopg.Connection,
    runtime_user: str,
    runtime_password: str,
    runtime_group: str,
) -> tuple[int]:
    r"""Execute the production psql script, emulating only its \gexec meta-command."""

    source = LOGIN_SQL.read_text(encoding="utf-8")
    source = source.replace("\\set ON_ERROR_STOP on\n", "", 1)
    for name, value in (
        ("runtime_user", runtime_user),
        ("runtime_password", runtime_password),
        ("runtime_group", runtime_group),
    ):
        source = source.replace(":'%s'" % name, sql.Literal(value).as_string(connection))

    chunks = source.split("\\gexec")
    assert len(chunks) > 2
    validation_prefix, first_generator = chunks[0].rsplit(";", 1)
    connection.execute(validation_prefix + ";")

    for generator in (first_generator, *chunks[1:-1]):
        for row in connection.execute(generator).fetchall():
            for statement in row:
                if statement:
                    connection.execute(statement)

    result = connection.execute(chunks[-1]).fetchone()
    assert result is not None
    return result


@pytest.mark.parametrize(
    ("surface", "runtime_group", "security_file", "table_name"),
    (
        ("api", "egoagentos_runtime", "security_roles.sql", "tasks"),
        (
            "bridge",
            "egoagentos_bridge_runtime",
            "agentteams_bridge_security.sql",
            "bridge_runs",
        ),
    ),
)
def test_runtime_login_hardening_removes_preexisting_direct_grants(
    postgres_url: str,
    surface: str,
    runtime_group: str,
    security_file: str,
    table_name: str,
) -> None:
    if surface == "api":
        PostgresStore(postgres_url)
    else:
        PostgresBridgeStore(postgres_url)

    runtime_user = "egoagentos_%s_direct_acl_test" % surface
    runtime_password = (surface + "-direct-acl-test-").ljust(64, "x")
    sequence_name = "egoagentos_%s_acl_probe_seq" % surface
    function_name = "egoagentos_%s_acl_probe_fn" % surface
    security_sql = (ROOT / "deploy/postgres" / security_file).read_text(encoding="utf-8")

    with psycopg.connect(postgres_url, autocommit=True) as connection:
        connection.execute(security_sql)
        database_name = connection.execute("SELECT current_database()").fetchone()[0]
        role_exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=%s", (runtime_user,)
        ).fetchone()
        if role_exists is None:
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(runtime_user), sql.Literal(runtime_password)
                )
            )
        else:
            connection.execute(
                sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(runtime_user), sql.Literal(runtime_password)
                )
            )

        connection.execute(sql.SQL("CREATE SEQUENCE {}").format(sql.Identifier(sequence_name)))
        connection.execute(
            sql.SQL("CREATE FUNCTION {}() RETURNS integer LANGUAGE sql AS 'SELECT 1'").format(
                sql.Identifier(function_name)
            )
        )
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON FUNCTION {}() FROM PUBLIC").format(
                sql.Identifier(function_name)
            )
        )
        connection.execute(
            sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(database_name)
            )
        )

        direct_grants = (
            sql.SQL("GRANT CONNECT, CREATE, TEMPORARY ON DATABASE {} TO {}").format(
                sql.Identifier(database_name), sql.Identifier(runtime_user)
            ),
            sql.SQL("GRANT CREATE, USAGE ON SCHEMA public TO {}").format(
                sql.Identifier(runtime_user)
            ),
            sql.SQL("GRANT DELETE ON TABLE {} TO {}").format(
                sql.Identifier(table_name), sql.Identifier(runtime_user)
            ),
            sql.SQL("GRANT UPDATE (id) ON TABLE {} TO {}").format(
                sql.Identifier(table_name), sql.Identifier(runtime_user)
            ),
            sql.SQL("GRANT USAGE, SELECT, UPDATE ON SEQUENCE {} TO {}").format(
                sql.Identifier(sequence_name), sql.Identifier(runtime_user)
            ),
            sql.SQL("GRANT EXECUTE ON FUNCTION {}() TO {}").format(
                sql.Identifier(function_name), sql.Identifier(runtime_user)
            ),
        )
        for statement in direct_grants:
            connection.execute(statement)

        assert _execute_runtime_login_sql(
            connection,
            runtime_user=runtime_user,
            runtime_password=runtime_password,
            runtime_group=runtime_group,
        ) == (1,)

        direct_acl_counts = connection.execute(
            """
            SELECT
              (SELECT count(*)
                 FROM pg_database object
                 CROSS JOIN LATERAL aclexplode(object.datacl) acl
                WHERE object.datname = current_database() AND acl.grantee = role.oid),
              (SELECT count(*)
                 FROM pg_namespace object
                 CROSS JOIN LATERAL aclexplode(object.nspacl) acl
                WHERE object.nspname = 'public' AND acl.grantee = role.oid),
              (SELECT count(*)
                 FROM pg_class object
                 JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
                 CROSS JOIN LATERAL aclexplode(object.relacl) acl
                WHERE namespace.nspname = 'public' AND acl.grantee = role.oid),
              (SELECT count(*)
                 FROM pg_attribute attribute
                 JOIN pg_class object ON object.oid = attribute.attrelid
                 JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
                 CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
                WHERE namespace.nspname = 'public' AND acl.grantee = role.oid),
              (SELECT count(*)
                 FROM pg_proc object
                 JOIN pg_namespace namespace ON namespace.oid = object.pronamespace
                 CROSS JOIN LATERAL aclexplode(object.proacl) acl
                WHERE namespace.nspname = 'public' AND acl.grantee = role.oid)
              FROM pg_roles role
             WHERE role.rolname = %s
            """,
            (runtime_user,),
        ).fetchone()
        assert direct_acl_counts == (0, 0, 0, 0, 0)

        effective_privileges = connection.execute(
            """
            SELECT
              login.rolcanlogin,
              authorization_group.rolcanlogin,
              has_database_privilege(login.rolname, current_database(), 'CONNECT'),
              has_database_privilege(login.rolname, current_database(), 'CREATE'),
              has_schema_privilege(login.rolname, 'public', 'CREATE'),
              has_table_privilege(login.rolname, %s, 'DELETE'),
              has_column_privilege(login.rolname, %s, 'id', 'UPDATE'),
              has_sequence_privilege(login.rolname, %s, 'USAGE'),
              has_function_privilege(login.rolname, %s, 'EXECUTE'),
              has_table_privilege(login.rolname, %s, 'SELECT')
              FROM pg_roles login
              JOIN pg_roles authorization_group ON authorization_group.rolname = %s
             WHERE login.rolname = %s
            """,
            (
                table_name,
                table_name,
                sequence_name,
                "public.%s()" % function_name,
                table_name,
                runtime_group,
                runtime_user,
            ),
        ).fetchone()
        assert effective_privileges == (
            True,
            False,
            True,
            False,
            False,
            False,
            False,
            False,
            False,
            True,
        )

        memberships = connection.execute(
            """
            SELECT array_agg(granted.rolname ORDER BY granted.rolname),
                   bool_or(membership.admin_option)
              FROM pg_auth_members membership
              JOIN pg_roles login ON login.oid = membership.member
              JOIN pg_roles granted ON granted.oid = membership.roleid
             WHERE login.rolname = %s
            """,
            (runtime_user,),
        ).fetchone()
        assert memberships == ([runtime_group], False)

    with psycopg.connect(_login_url(postgres_url, runtime_user, runtime_password)) as runtime:
        assert runtime.execute("SELECT current_user").fetchone() == (runtime_user,)
