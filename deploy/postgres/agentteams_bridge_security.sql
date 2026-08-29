-- Run as a database/platform administrator after the checksummed bridge migration.
-- Role creation may require privileges beyond the bridge migration owner. Create a
-- LOGIN identity separately through the platform secret manager, then grant this
-- NOLOGIN role to it. The runtime role must not own these tables or functions.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'egoagentos_bridge_runtime') THEN
        CREATE ROLE egoagentos_bridge_runtime NOLOGIN;
    END IF;
END
$$;

ALTER ROLE egoagentos_bridge_runtime
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

DO $$
BEGIN
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO egoagentos_bridge_runtime',
        current_database()
    );
END
$$;

GRANT USAGE ON SCHEMA public TO egoagentos_bridge_runtime;
REVOKE ALL ON bridge_runs, bridge_events, bridge_receipts, bridge_schema_migrations
    FROM egoagentos_bridge_runtime;

GRANT SELECT, INSERT ON bridge_runs TO egoagentos_bridge_runtime;
GRANT UPDATE(state, task_graph, checkpoint, version, updated_at)
    ON bridge_runs TO egoagentos_bridge_runtime;
GRANT SELECT, INSERT ON bridge_events, bridge_receipts TO egoagentos_bridge_runtime;
GRANT SELECT ON bridge_schema_migrations TO egoagentos_bridge_runtime;

REVOKE ALL ON FUNCTION egoagentos_bridge_guard_event_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION egoagentos_bridge_guard_receipt_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION egoagentos_bridge_reject_ledger_mutation() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION egoagentos_bridge_guard_event_insert()
    TO egoagentos_bridge_runtime;
GRANT EXECUTE ON FUNCTION egoagentos_bridge_guard_receipt_insert()
    TO egoagentos_bridge_runtime;
GRANT EXECUTE ON FUNCTION egoagentos_bridge_reject_ledger_mutation()
    TO egoagentos_bridge_runtime;
