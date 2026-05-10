#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════
# db/migrations/APPLY.sh — apply jobHunt migrations in order
# ─────────────────────────────────────────────────────────────────────────
# What it does
#   * Reads every *.sql file in db/migrations/ in lexical order.
#   * Tracks applied filenames in a `schema_migrations` log table; skips
#     anything already there. Safe to re-run.
#   * Refuses to run against an obviously-production DATABASE_URL without
#     an explicit "yes" confirmation.
#
# Usage
#   export DATABASE_URL='postgres://...'
#   bash db/migrations/APPLY.sh
#
# Requirements
#   * psql on PATH
#   * DATABASE_URL set (postgres connection string)
# ═════════════════════════════════════════════════════════════════════════

set -euo pipefail

# Resolve repo paths off the script location so this works from anywhere.
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
DB_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
MIGRATIONS_DIR="$SCRIPT_DIR"

# ── 0. sanity ────────────────────────────────────────────────────────────
if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "FATAL: DATABASE_URL is not set." >&2
    echo "  export DATABASE_URL='postgres://...'  # Supabase Connection String" >&2
    exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
    echo "FATAL: psql is not on PATH." >&2
    exit 1
fi

# ── 1. production confirmation ───────────────────────────────────────────
# Heuristic: if the URL doesn't contain 'localhost' or 'dev', treat it as
# prod-ish and demand a typed confirmation.
LOWER_URL="$(echo "$DATABASE_URL" | tr '[:upper:]' '[:lower:]')"
case "$LOWER_URL" in
    *localhost*|*127.0.0.1*|*"@dev"*|*"-dev."*|*"-dev-"*|*".dev."*|*"_dev_"*)
        IS_PROD_LIKE=0
        ;;
    *)
        IS_PROD_LIKE=1
        ;;
esac

if [[ "$IS_PROD_LIKE" == "1" ]]; then
    echo "DATABASE_URL does not look like a local/dev database."
    echo "  host fragment: $(echo "$DATABASE_URL" | sed -E 's|^[^@]+@([^/]+)/.*|\1|')"
    echo
    echo "Have you tested these migrations on a Supabase branch first? (Section in README.md)"
    read -r -p 'Type "yes" to apply to this database: ' confirm
    if [[ "$confirm" != "yes" ]]; then
        echo "Aborted by user." >&2
        exit 1
    fi
fi

# ── 2. ensure schema_migrations log table ────────────────────────────────
psql "$DATABASE_URL" \
    --set ON_ERROR_STOP=on \
    --quiet --no-psqlrc \
    -c "CREATE TABLE IF NOT EXISTS schema_migrations (
            filename    TEXT PRIMARY KEY,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            checksum    TEXT
        );" >/dev/null

# ── 3. iterate migrations ────────────────────────────────────────────────
shopt -s nullglob
files=( "$MIGRATIONS_DIR"/*.sql )
shopt -u nullglob

if [[ ${#files[@]} -eq 0 ]]; then
    echo "No migrations found in $MIGRATIONS_DIR" >&2
    exit 0
fi

# Ensure lexical order regardless of glob order.
IFS=$'\n' sorted=( $(printf '%s\n' "${files[@]}" | sort) )
unset IFS

applied_count=0
skipped_count=0
for path in "${sorted[@]}"; do
    name="$(basename "$path")"

    # Skip the README and any non-SQL files defensively.
    [[ "$name" == *.sql ]] || continue

    # Already applied?
    is_applied=$(psql "$DATABASE_URL" \
        --tuples-only --no-align --quiet --no-psqlrc \
        --set ON_ERROR_STOP=on \
        -c "SELECT 1 FROM schema_migrations WHERE filename = '$name';")

    if [[ "$(echo "$is_applied" | tr -d '[:space:]')" == "1" ]]; then
        echo "skip  $name (already applied)"
        skipped_count=$((skipped_count + 1))
        continue
    fi

    echo "apply $name"
    # Compute a checksum so we can detect drift later (md5 portable across
    # macOS and Linux: prefer md5sum, fall back to md5 -q on macOS).
    if command -v md5sum >/dev/null 2>&1; then
        sum="$(md5sum "$path" | awk '{print $1}')"
    elif command -v md5 >/dev/null 2>&1; then
        sum="$(md5 -q "$path")"
    else
        sum=""
    fi

    psql "$DATABASE_URL" \
        --set ON_ERROR_STOP=on \
        --quiet --no-psqlrc \
        -f "$path"

    psql "$DATABASE_URL" \
        --set ON_ERROR_STOP=on \
        --quiet --no-psqlrc \
        -c "INSERT INTO schema_migrations (filename, checksum) VALUES ('$name', '$sum')
            ON CONFLICT (filename) DO NOTHING;" >/dev/null

    applied_count=$((applied_count + 1))
done

echo
echo "Done. applied=$applied_count  skipped=$skipped_count"

# ── 4. final hint ────────────────────────────────────────────────────────
echo
echo "Tip: if this is the first time you're using APPLY.sh, mark it executable:"
echo "    chmod +x \"$SCRIPT_DIR/APPLY.sh\""
echo "After 001 has been applied, seed the founder user:"
echo "    psql \"\$DATABASE_URL\" -f \"$DB_DIR/seeds/user_001.sql\""
