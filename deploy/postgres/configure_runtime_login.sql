\set ON_ERROR_STOP on

-- A reused identity that owns database objects could alter tables or disable triggers.
-- Fail before changing its password or privileges rather than silently accepting that bypass.
SELECT 1 / CASE WHEN EXISTS (
    SELECT 1 FROM pg_database database_object
     JOIN pg_roles owner_role ON owner_role.oid = database_object.datdba
    WHERE owner_role.rolname = :'runtime_user'
    UNION ALL
    SELECT 1 FROM pg_namespace schema_object
     JOIN pg_roles owner_role ON owner_role.oid = schema_object.nspowner
    WHERE owner_role.rolname = :'runtime_user'
    UNION ALL
    SELECT 1 FROM pg_class relation_object
     JOIN pg_roles owner_role ON owner_role.oid = relation_object.relowner
    WHERE owner_role.rolname = :'runtime_user'
    UNION ALL
    SELECT 1 FROM pg_proc function_object
     JOIN pg_roles owner_role ON owner_role.oid = function_object.proowner
    WHERE owner_role.rolname = :'runtime_user'
) THEN 0 ELSE 1 END AS runtime_login_owns_no_database_objects;

-- Values are supplied with psql --set by configure_runtime_login.sh. format(%I/%L)
-- keeps both the role name and password out of hand-built SQL strings.
SELECT format(
    'CREATE ROLE %I LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE '
    'NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'runtime_user',
    :'runtime_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'runtime_user')
\gexec

SELECT format(
    'ALTER ROLE %I WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE '
    'NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'runtime_user',
    :'runtime_password'
)
\gexec

-- A LOGIN is only an authentication identity. Remove every direct ACL it may
-- have accumulated in this database before attaching the intended NOLOGIN
-- authorization group. CONNECT remains available through that group.
SELECT format(
    'REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I',
    current_database(),
    :'runtime_user'
)
\gexec

SELECT format('REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I', :'runtime_user')
\gexec

SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %I',
    :'runtime_user'
)
\gexec

-- Table-level REVOKE does not remove grants made on individual columns.
SELECT format(
    'REVOKE %s (%I) ON TABLE %I.%I FROM %I',
    column_acl.privilege_type,
    attribute.attname,
    namespace.nspname,
    relation.relname,
    :'runtime_user'
)
  FROM pg_attribute attribute
  JOIN pg_class relation ON relation.oid = attribute.attrelid
  JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
  CROSS JOIN LATERAL aclexplode(attribute.attacl) column_acl
  JOIN pg_roles direct_grantee ON direct_grantee.oid = column_acl.grantee
 WHERE namespace.nspname = 'public'
   AND direct_grantee.rolname = :'runtime_user'
   AND NOT attribute.attisdropped
   AND attribute.attnum > 0
   AND column_acl.privilege_type IN ('INSERT', 'REFERENCES', 'SELECT', 'UPDATE')
\gexec

SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM %I',
    :'runtime_user'
)
\gexec

SELECT format(
    'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM %I',
    :'runtime_user'
)
\gexec

-- Remove stale memberships before granting exactly one least-privilege group role.
SELECT format('REVOKE %I FROM %I', granted_role.rolname, member_role.rolname)
  FROM pg_auth_members membership
  JOIN pg_roles granted_role ON granted_role.oid = membership.roleid
  JOIN pg_roles member_role ON member_role.oid = membership.member
 WHERE member_role.rolname = :'runtime_user'
   AND granted_role.rolname <> :'runtime_group'
\gexec

SELECT format(
    'REVOKE ADMIN OPTION FOR %I FROM %I', granted_role.rolname, member_role.rolname
)
  FROM pg_auth_members membership
  JOIN pg_roles granted_role ON granted_role.oid = membership.roleid
  JOIN pg_roles member_role ON member_role.oid = membership.member
 WHERE member_role.rolname = :'runtime_user'
   AND granted_role.rolname = :'runtime_group'
   AND membership.admin_option
\gexec

SELECT format('GRANT %I TO %I', :'runtime_group', :'runtime_user')
\gexec

SELECT 1 / CASE WHEN EXISTS (
    SELECT 1
      FROM pg_roles role_state
      JOIN pg_roles group_state ON group_state.rolname = :'runtime_group'
     WHERE role_state.rolname = :'runtime_user'
       AND role_state.rolcanlogin
       AND role_state.rolinherit
       AND NOT role_state.rolsuper
       AND NOT role_state.rolcreatedb
       AND NOT role_state.rolcreaterole
       AND NOT role_state.rolreplication
       AND NOT role_state.rolbypassrls
       AND NOT group_state.rolcanlogin
       AND NOT group_state.rolsuper
       AND NOT group_state.rolcreatedb
       AND NOT group_state.rolcreaterole
       AND NOT group_state.rolreplication
       AND NOT group_state.rolbypassrls
       AND has_database_privilege(role_state.rolname, current_database(), 'CONNECT')
       AND (
           SELECT count(*)
             FROM pg_auth_members membership
             JOIN pg_roles granted_role ON granted_role.oid = membership.roleid
            WHERE membership.member = role_state.oid
       ) = 1
       AND pg_has_role(role_state.oid, group_state.oid, 'MEMBER')
       AND NOT EXISTS (
           SELECT 1
             FROM pg_auth_members membership
            WHERE membership.member = role_state.oid
              AND membership.admin_option
       )
       AND NOT EXISTS (
           SELECT 1
             FROM pg_database database_object
             CROSS JOIN LATERAL aclexplode(database_object.datacl) direct_acl
            WHERE database_object.datname = current_database()
              AND direct_acl.grantee = role_state.oid
       )
       AND NOT EXISTS (
           SELECT 1
             FROM pg_namespace schema_object
             CROSS JOIN LATERAL aclexplode(schema_object.nspacl) direct_acl
            WHERE schema_object.nspname = 'public'
              AND direct_acl.grantee = role_state.oid
       )
       AND NOT EXISTS (
           SELECT 1
             FROM pg_class relation_object
             JOIN pg_namespace namespace
               ON namespace.oid = relation_object.relnamespace
             CROSS JOIN LATERAL aclexplode(relation_object.relacl) direct_acl
            WHERE namespace.nspname = 'public'
              AND direct_acl.grantee = role_state.oid
       )
       AND NOT EXISTS (
           SELECT 1
             FROM pg_attribute attribute
             JOIN pg_class relation_object ON relation_object.oid = attribute.attrelid
             JOIN pg_namespace namespace
               ON namespace.oid = relation_object.relnamespace
             CROSS JOIN LATERAL aclexplode(attribute.attacl) direct_acl
            WHERE namespace.nspname = 'public'
              AND direct_acl.grantee = role_state.oid
       )
       AND NOT EXISTS (
           SELECT 1
             FROM pg_proc function_object
             JOIN pg_namespace namespace
               ON namespace.oid = function_object.pronamespace
             CROSS JOIN LATERAL aclexplode(function_object.proacl) direct_acl
            WHERE namespace.nspname = 'public'
              AND direct_acl.grantee = role_state.oid
       )
) THEN 1 ELSE 0 END AS runtime_login_is_restricted;
