#!/bin/bash
# Terminal 2 : Gateway API

cd "$(dirname "$0")/.."

# Créer venv si n'existe pas
if [ ! -d ".venv" ]; then
    echo "Creation du venv..."
    python3 -m venv .venv
fi

# Activer le venv
source .venv/bin/activate

# Toujours installer/mettre à jour les dépendances
echo "Installation des dependances..."
pip install -r requirements.txt
pip install 'pydantic[email]'

# Générer le client Prisma si nécessaire
if [ ! -f ".venv/.prisma_generated" ] || [ "prisma/schema.prisma" -nt ".venv/.prisma_generated" ]; then
    echo "Generation du client Prisma..."
    prisma generate
    touch .venv/.prisma_generated
fi

echo "Lancement de l'API sur http://localhost:8100..."
uvicorn app.main:app --reload --host 0.0.0.0 --port 8100
