#!/bin/bash
# Terminal 3 : Approval Worker

cd "$(dirname "$0")/../approval_worker"

# Créer venv si n'existe pas
if [ ! -d "venv" ]; then
    echo "Creation du venv..."
    python3 -m venv venv
fi

# Activer
source venv/bin/activate

# Installer les dépendances si besoin
if [ ! -f "venv/.installed" ]; then
    echo "Installation des dependances..."
    pip install -r requirements.txt
    touch venv/.installed
fi

echo "Lancement du Worker sur http://localhost:5001..."
python app.py
