"""Configuration model and loader for continuous entitlement synchronization."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class MidPointSyncConfig(BaseModel):
    resource_oid: str = Field(min_length=1)
    import_shadows_on_addition: bool = True
    delete_shadows_on_disappearance: bool = True
    object_classes: dict[str, str] = Field(default_factory=dict)


class EntitlementSyncConfig(BaseModel):
    version: int = 1
    enabled: bool = True
    poll_interval_seconds: int = Field(default=60, ge=5)
    startup_delay_seconds: int = Field(default=15, ge=0)
    request_timeout_seconds: int = Field(default=30, ge=1)
    decommission_timeout_seconds: int = Field(default=600, ge=30)
    gateway_http_url: str = "http://gateway-http:5100"
    gateway_api_url: str = "http://gateway-api:8100"
    missing_confirmation_cycles: int = Field(default=3, ge=1)
    state_file: str = "/app/data/entitlement-sync-state.json"
    midpoint: MidPointSyncConfig


def load_entitlement_sync_config(path: str | Path) -> EntitlementSyncConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return EntitlementSyncConfig.model_validate(payload)
