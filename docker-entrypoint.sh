#!/bin/sh
set -e

# Default DB path fallback if unset
DB_FILE="${VECTIS_DB_PATH:-/app/data/vectis.db}"

# Auto-initialize database if missing and not explicitly running init
if [ ! -f "$DB_FILE" ] && [ "$1" != "init" ]; then
    echo "[vectis] Database not found at $DB_FILE. Initializing default schema and agents..."
    vectis init
fi

# If first argument matches a vectis subcommand or option flag, route through vectis
case "$1" in
    run|ui|status|seed|agents|tickets|budget|logs|tree|workspace|monitor|init|-*)
        exec vectis "$@"
        ;;
    *)
        exec "$@"
        ;;
esac
