#!/bin/bash

# Script d'export des bases de données Gateway IAM
# Usage: ./scripts/dump_databases.sh
# Les dumps sont sauvegardés dans dumps/

set -e

DUMP_DIR="$(cd "$(dirname "$0")/.." && pwd)/dumps"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DUMP_FOLDER="$DUMP_DIR/$TIMESTAMP"

mkdir -p "$DUMP_FOLDER"
echo "Dossier de sauvegarde : $DUMP_FOLDER"
echo ""

# ── 1. PostgreSQL gateway (gateway_iam) ──────────────────────────────────────
echo "[1/3] Dump schéma PostgreSQL gateway (gateway_iam)..."
docker exec gitivity-postgres pg_dump --schema-only -U gateway gateway_iam \
    > "$DUMP_FOLDER/gateway_iam.sql" \
    && echo "      OK -> gateway_iam.sql" \
    || echo "      ERREUR - le conteneur gitivity-postgres est-il démarré ?"

# ── 2. PostgreSQL cible (target_db) ──────────────────────────────────────────
echo "[2/3] Dump schéma PostgreSQL cible (target_db)..."
docker exec gitivity-postgresql pg_dump --schema-only -U target_user target_db \
    > "$DUMP_FOLDER/postgresql_target_db.sql" \
    && echo "      OK -> postgresql_target_db.sql" \
    || echo "      ERREUR - le conteneur gitivity-postgresql est-il démarré ?"

# ── 3. MySQL cible (target_db) ───────────────────────────────────────────────
echo "[3/3] Dump schéma MySQL cible (target_db)..."
docker exec gitivity-mysql mysqldump --no-data -u root -pmysql_root_secret target_db \
    > "$DUMP_FOLDER/mysql_target_db.sql" \
    && echo "      OK -> mysql_target_db.sql" \
    || echo "      ERREUR - le conteneur gitivity-mysql est-il démarré ?"

# ── Résumé ───────────────────────────────────────────────────────────────────
echo ""
echo "Dumps générés dans : $DUMP_FOLDER"
ls -lh "$DUMP_FOLDER"
