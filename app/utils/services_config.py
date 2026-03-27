"""Per-service approval configuration helper (no circular imports)"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "services_approval.json"


def service_requires_approval(service_name: str) -> bool:
    """Returns True if the service requires email approval (default: True)."""
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            config = json.load(f).get("services", {})
        return config.get(service_name, True)
    except FileNotFoundError:
        return True
