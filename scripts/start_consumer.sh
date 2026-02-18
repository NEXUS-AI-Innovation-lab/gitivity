#!/bin/bash
# Terminal 1 : Gateway Consumer

cd "$(dirname "$0")/.."

# Créer venv si n'existe pas
if [ ! -d ".venv" ]; then
    echo "Creation du venv..."
    python3 -m venv .venv
fi

# Activer
source .venv/bin/activate

# Installer les dépendances si besoin
if [ ! -f ".venv/.installed" ]; then
    echo "Installation des dependances..."
    pip install -r requirements.txt
    prisma generate
    prisma db push
    touch .venv/.installed
fi

echo "Lancement du Consumer..."
python scripts/start_consumer.py