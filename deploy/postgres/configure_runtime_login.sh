#!/bin/sh
set -eu

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${EGO_RUNTIME_USER:?EGO_RUNTIME_USER is required}"
: "${EGO_RUNTIME_PASSWORD:?EGO_RUNTIME_PASSWORD is required}"
: "${EGO_RUNTIME_GROUP:?EGO_RUNTIME_GROUP is required}"
: "${EGO_SECURITY_SQL:?EGO_SECURITY_SQL is required}"

case "$EGO_RUNTIME_USER" in
    *[!A-Za-z0-9_]*|'')
        echo "EGO_RUNTIME_USER must be a simple PostgreSQL identifier" >&2
        exit 2
        ;;
esac
case "$EGO_RUNTIME_GROUP" in
    *[!A-Za-z0-9_]*|'')
        echo "EGO_RUNTIME_GROUP must be a simple PostgreSQL identifier" >&2
        exit 2
        ;;
esac

if [ "$EGO_RUNTIME_USER" = "$POSTGRES_USER" ]; then
    echo "runtime login must differ from POSTGRES_USER migration owner" >&2
    exit 2
fi
if [ "$EGO_RUNTIME_PASSWORD" = "$POSTGRES_PASSWORD" ]; then
    echo "runtime password must differ from POSTGRES_PASSWORD migration secret" >&2
    exit 2
fi
if [ "${#EGO_RUNTIME_PASSWORD}" -lt 32 ]; then
    echo "runtime password must contain at least 32 characters" >&2
    exit 2
fi
if [ -n "${EGO_PEER_RUNTIME_USER:-}" ] && [ "$EGO_RUNTIME_USER" = "$EGO_PEER_RUNTIME_USER" ]; then
    echo "API and bridge runtime logins must be distinct" >&2
    exit 2
fi
if [ -n "${EGO_PEER_RUNTIME_PASSWORD:-}" ] \
    && [ "$EGO_RUNTIME_PASSWORD" = "$EGO_PEER_RUNTIME_PASSWORD" ]; then
    echo "API and bridge runtime passwords must be distinct" >&2
    exit 2
fi
if [ ! -r "$EGO_SECURITY_SQL" ]; then
    echo "security SQL is not readable: $EGO_SECURITY_SQL" >&2
    exit 2
fi

export PGPASSWORD="$POSTGRES_PASSWORD"
psql --host="${PGHOST:-postgres}" --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" --set=ON_ERROR_STOP=1 --file="$EGO_SECURITY_SQL"
psql --host="${PGHOST:-postgres}" --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" --set=ON_ERROR_STOP=1 \
    --set=runtime_user="$EGO_RUNTIME_USER" \
    --set=runtime_password="$EGO_RUNTIME_PASSWORD" \
    --set=runtime_group="$EGO_RUNTIME_GROUP" \
    --file=/opt/egoagentos-postgres/configure_runtime_login.sql
