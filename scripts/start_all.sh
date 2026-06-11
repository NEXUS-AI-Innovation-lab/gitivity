#!/bin/bash
# Lance les 4 services dans UN seul terminal (Putty)
# Ctrl+C pour tout arreter

SCRIPT_DIR="$(dirname "$0")"
PROJECT_DIR="$SCRIPT_DIR/.."
GATEWAY_HTTP_DIR="$PROJECT_DIR/gateway-http"

# Fonction de nettoyage : tue tous les process enfants
cleanup() {
    echo ""
    echo "Arret de tous les services..."
    kill $(jobs -p) 2>/dev/null
    wait 2>/dev/null
    echo "Tous les services sont arretes."
    exit 0
}
trap cleanup SIGINT SIGTERM

echo "=========================================="
echo "  Gateway IAM - Demarrage complet"
echo "=========================================="

# --- Preparation du venv principal ---
cd "$PROJECT_DIR"
if [ ! -d ".venv" ]; then
    echo "[SETUP] Creation du venv..."
    python3 -m venv .venv
fi
source .venv/bin/activate

if [ ! -f ".venv/.installed" ] || [ "requirements.txt" -nt ".venv/.installed" ]; then
    echo "[SETUP] Installation des dependances..."
    pip install -r requirements.txt
    touch .venv/.installed
fi

if [ ! -f ".venv/.prisma_generated" ] || [ "prisma/schema.prisma" -nt ".venv/.prisma_generated" ]; then
    echo "[SETUP] Generation du client Prisma..."
    prisma generate
    prisma db push
    touch .venv/.prisma_generated
fi


echo "[1/4] Gateway HTTP       -> http://localhost:5100"
python3 "$GATEWAY_HTTP_DIR/gateway-http.py" &

# --- 2. Consumer RabbitMQ ---
sleep 1
echo "[2/4] Consumer RabbitMQ  -> ecoute midpoint-operations"
python3 scripts/start_consumer.py &

# --- 3. API FastAPI (port 8100) ---
sleep 2
echo "[3/4] API FastAPI        -> http://localhost:8100"
uvicorn app.main:app --host 0.0.0.0 --port 8100 &

# --- 4. Dashboard (port 5102) ---
sleep 1
echo "[4/4] Dashboard          -> http://localhost:5102"
python3 -m streamlit run connector-dashboard/app.py --server.port 5102 --server.headless true 2>/dev/null &

echo ""
echo "=========================================="
echo "  Tous les services sont lances !"
echo "  Ctrl+C pour tout arreter"
echo "=========================================="
echo ""

# Attend que tous les process tournent
wait
