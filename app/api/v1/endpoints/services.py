"""API endpoints for per-service approval configuration"""
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/services", tags=["services"])

DATA_FILE = Path(__file__).resolve().parents[4] / "data" / "services_approval.json"

KNOWN_SERVICES = ["LDAP", "PostgreSQL", "MySQL", "Odoo"]


def _read_config() -> dict[str, bool]:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("services", {})
    except FileNotFoundError:
        return {s: True for s in KNOWN_SERVICES}


def _write_config(services: dict[str, bool]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"services": services}, f, indent=2, ensure_ascii=False)


def service_requires_approval(service_name: str) -> bool:
    """Returns True if the service requires email approval (default: True)."""
    return _read_config().get(service_name, True)


class ServiceConfig(BaseModel):
    requires_approval: bool


@router.get("/approval-config")
async def get_approval_config():
    """Get approval requirement for all services"""
    config = _read_config()
    # Ensure all known services appear in the response
    for s in KNOWN_SERVICES:
        if s not in config:
            config[s] = True
    return config


@router.put("/approval-config/{service_name}", status_code=status.HTTP_200_OK)
async def update_service_approval(service_name: str, data: ServiceConfig):
    """Enable or disable email approval for a specific service"""
    config = _read_config()
    config[service_name] = data.requires_approval
    _write_config(config)
    logger.info(f"Service '{service_name}' approval set to {data.requires_approval}")
    return {"service": service_name, "requires_approval": data.requires_approval}
