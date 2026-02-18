#!/bin/bash
set -e

echo "=== Gateway IAM Entrypoint ==="

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
else
    echo "Unknown command: $1"
    echo "Usage: entrypoint.sh [api|consumer]"
    exit 1
fi
