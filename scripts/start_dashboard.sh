#!/bin/bash
# Terminal 4 : Connector Dashboard

cd "$(dirname "$0")/../connector-dashboard"

# Créer venv si n'existe pas
if [ ! -d "venv" ]; then
    echo "Creation du venv..."
    python3 -m venv venv
fi

# Activer
source venv/bin/activate

# Installer les dépendances si besoin (ou si requirements.txt a changé)
if [ ! -f "venv/.installed" ] || [ "requirements.txt" -nt "venv/.installed" ]; then
    echo "Installation des dependances..."
    pip install -r requirements.txt
    touch venv/.installed
fi

echo "Lancement du Dashboard sur http://localhost:5002..."
python app.py
