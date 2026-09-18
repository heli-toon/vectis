#!/bin/bash
# Vectis execution logs cleanup script
# Deletes execution logs older than RETENTION_DAYS

# Configuration
RETENTION_DAYS=7
DB_PATH="vectis.db"  # default, but can be overridden by environment

# Allow override via environment
if [ -n "$VECTIS_DB_PATH" ]; then
    DB_PATH="$VECTIS_DB_PATH"
fi

echo "Cleaning execution logs older than $RETENTION_DAYS days from $DB_PATH"

# SQLite query to delete old logs
sqlite3 "$DB_PATH" "DELETE FROM execution_logs WHERE executed_at < datetime('now', '-$RETENTION_DAYS days');"

# Optional: Vacuum to reclaim space
sqlite3 "$DB_PATH" "VACUUM;"

echo "Cleanup complete."