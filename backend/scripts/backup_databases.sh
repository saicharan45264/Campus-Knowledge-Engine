#!/usr/bin/env bash
# =============================================================================
# M.A.C.H. AI — Automated Campus Database Backup Runner
# Executes daily snapshots of PostgreSQL (pgvector) and Neo4j knowledge graph.
# Retains snapshots for 14 days and prunes older archives.
# =============================================================================

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups/mach_ai}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-cl-postgres}"
NEO4J_CONTAINER="${NEO4J_CONTAINER:-cl-neo4j}"
DB_USER="${POSTGRES_USER:-mach}"
DB_NAME="${POSTGRES_DB:-mach_ai}"

mkdir -p "${BACKUP_DIR}"

echo "[${TIMESTAMP}] Initiating M.A.C.H. AI database backup..."

# 1. Backup PostgreSQL 16 pgvector database
PG_BACKUP_FILE="${BACKUP_DIR}/postgres_${DB_NAME}_${TIMESTAMP}.dump"
if docker ps --format '{{.Names}}' | grep -q "^${POSTGRES_CONTAINER}$"; then
    echo "Dumping PostgreSQL database: ${DB_NAME} from container: ${POSTGRES_CONTAINER}..."
    docker exec "${POSTGRES_CONTAINER}" pg_dump -U "${DB_USER}" -d "${DB_NAME}" -F c -b -v -f "/tmp/pg_backup.dump"
    docker cp "${POSTGRES_CONTAINER}:/tmp/pg_backup.dump" "${PG_BACKUP_FILE}"
    docker exec "${POSTGRES_CONTAINER}" rm -f "/tmp/pg_backup.dump"
    echo "PostgreSQL backup successfully written to: ${PG_BACKUP_FILE}"
else
    echo "Warning: Container ${POSTGRES_CONTAINER} not running. Attempting direct host pg_dump..."
    if command -v pg_dump >/dev/null 2>&1; then
        pg_dump -h "${POSTGRES_HOST:-localhost}" -p "${POSTGRES_PORT:-5434}" -U "${DB_USER}" -d "${DB_NAME}" -F c -f "${PG_BACKUP_FILE}"
        echo "Host pg_dump successfully written to: ${PG_BACKUP_FILE}"
    else
        echo "Error: Neither Docker container nor host pg_dump is accessible."
    fi
fi

# 2. Backup Neo4j Property Graph
NEO4J_BACKUP_FILE="${BACKUP_DIR}/neo4j_${TIMESTAMP}.dump"
if docker ps --format '{{.Names}}' | grep -q "^${NEO4J_CONTAINER}$"; then
    echo "Creating Neo4j database dump from container: ${NEO4J_CONTAINER}..."
    docker exec "${NEO4J_CONTAINER}" neo4j-admin database dump neo4j --to-path="/tmp"
    docker cp "${NEO4J_CONTAINER}:/tmp/neo4j.dump" "${NEO4J_BACKUP_FILE}"
    docker exec "${NEO4J_CONTAINER}" rm -f "/tmp/neo4j.dump"
    echo "Neo4j backup successfully written to: ${NEO4J_BACKUP_FILE}"
else
    echo "Notice: Container ${NEO4J_CONTAINER} not running; skipping Neo4j container dump."
fi

# 3. Retain last 14 days of backups and prune older files
echo "Pruning backup archives older than 14 days in ${BACKUP_DIR}..."
find "${BACKUP_DIR}" -type f \( -name "postgres_*.dump" -o -name "neo4j_*.dump" \) -mtime +14 -delete

echo "[$(date +"%Y%m%d_%H%M%S")] M.A.C.H. AI database backup completed successfully."

