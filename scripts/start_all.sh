#!/bin/bash
# Lance les 5 services dans UN seul terminal (Putty)
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


echo "[1/5] Gateway HTTP       -> http://localhost:5100"
python3 "$GATEWAY_HTTP_DIR/gateway-http.py" &

# --- 2. Consumer RabbitMQ ---
sleep 1
echo "[2/5] Consumer RabbitMQ  -> ecoute midpoint-operations"
python3 scripts/start_consumer.py &

# --- 3. API FastAPI (port 8100) ---
sleep 2
echo "[3/5] API FastAPI        -> http://localhost:8100"
uvicorn app.main:app --host 0.0.0.0 --port 8100 &

# --- 4. Approval Worker (port 5101) ---
echo "[4/5] Approval Worker    -> http://localhost:5101"
cd "$PROJECT_DIR/approval_worker"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
if [ ! -f "venv/.installed" ]; then
    pip install -r requirements.txt
    touch venv/.installed
fi
python3 app.py &
cd "$PROJECT_DIR"
source .venv/bin/activate

# --- 5. Dashboard (port 5102) ---
sleep 1
echo "[5/5] Dashboard          -> http://localhost:5102"
python3 -m streamlit run connector-dashboard/app.py --server.port 5102 --server.headless true 2>/dev/null &

echo ""
echo "=========================================="
echo "  Tous les services sont lances !"
echo "  Ctrl+C pour tout arreter"
echo "=========================================="
echo ""

# Attend que tous les process tournent
wait
