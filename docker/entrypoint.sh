#!/bin/bash
set -e

echo "=== Gateway IAM Entrypoint ==="

# Ensure approvers.json exists (bind mount may shadow the image's copy)
if [ ! -f /app/data/approvers.json ]; then
    echo "Initializing missing approvers.json..."
    mkdir -p /app/data
    echo '{"approvers":[]}' > /app/data/approvers.json
fi

# Wait for database to be ready (retry loop)
echo "Waiting for database..."
for i in $(seq 1 30); do
    if prisma db push --skip-generate 2>/dev/null; then
        echo "Database ready and schema pushed."
        break
    fi
    echo "Database not ready yet (attempt $i/30)..."
    sleep 2
done

if [ "$1" = "api" ]; then
    echo "Starting FastAPI API on port 8100..."
    exec uvicorn app.main:app --host 0.0.0.0 --port 8100
elif [ "$1" = "consumer" ]; then
    echo "Starting RabbitMQ Consumer..."
    exec python scripts/start_consumer.py
elif [ "$1" = "entitlement-sync" ]; then
    echo "Starting continuous entitlement synchronization..."
    exec python -m scripts.start_entitlement_sync
else
    echo "Unknown command: $1"
    echo "Usage: entrypoint.sh [api|consumer|entitlement-sync]"
    exit 1
fi
