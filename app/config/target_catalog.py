"""Declarative target catalogue with environment interpolation and hot reload."""

from __future__ import annotations

import os
import re
from pathlib import Path
from threading import RLock
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from app.utils.enums import TargetService

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "targets.yaml"


def _interpolate(value: Any) -> Any:
    """Resolve ${VAR} and ${VAR:-default}, preserving scalar YAML types."""
    if isinstance(value, dict):
        return {key: _interpolate(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate(item) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        if name in os.environ:
            return os.environ[name]
        return default or ""

    resolved = _ENV_PATTERN.sub(replace, value)
    if _ENV_PATTERN.fullmatch(value):
        # Convert "false", "10", etc. to their natural YAML type.
        return yaml.safe_load(resolved) if resolved != "" else ""
    return resolved


class TargetRouting(BaseModel):
    """How MidPoint attributes select a target."""

    entitlement_attributes: list[str] = Field(default_factory=list)
    delete_mode: str = "delete"

    @field_validator("delete_mode")
    @classmethod
    def validate_delete_mode(cls, value: str) -> str:
        if value not in {"delete", "update"}:
            raise ValueError("delete_mode must be 'delete' or 'update'")
        return value


class EntitlementConfig(BaseModel):
    """Optional entitlement discovery metadata."""

    provider: str
    identifier: str = "{name}"
    fallback: list[str] = Field(default_factory=list)


class TargetDefinition(BaseModel):
    """One configured target instance."""

    id: str
    type: str
    display_name: str
    enabled: bool = True
    aliases: list[str] = Field(default_factory=list)
    connection: dict[str, Any] = Field(default_factory=dict)
    provisioning: dict[str, Any] = Field(default_factory=dict)
    routing: TargetRouting = Field(default_factory=TargetRouting)
    entitlements: EntitlementConfig | None = None

    @field_validator("id", "type")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", normalized):
            raise ValueError("must match [a-z][a-z0-9_-]*")
        return normalized

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip().lower() for value in values if value.strip()))

    @property
    def family(self) -> TargetService:
        try:
            return TargetService(self.type.upper())
        except ValueError as exc:
            raise ValueError(
                f"Target '{self.id}' uses unsupported connector type '{self.type}'"
            ) from exc

    def public_connection(self) -> dict[str, Any]:
        """Return connection data with secrets removed."""
        secret_fragments = ("password", "secret", "token", "key")
        return {
            key: value
            for key, value in self.connection.items()
            if not any(fragment in key.lower() for fragment in secret_fragments)
        }


class TargetCatalogDocument(BaseModel):
    version: int = 1
    targets: list[TargetDefinition]

    @model_validator(mode="after")
    def validate_uniqueness(self) -> TargetCatalogDocument:
        identifiers: dict[str, str] = {}
        for target in self.targets:
            for identifier in [target.id, *target.aliases]:
                owner = identifiers.get(identifier)
                if owner and owner != target.id:
                    raise ValueError(
                        f"Identifier '{identifier}' is shared by '{owner}' and '{target.id}'"
                    )
                identifiers[identifier] = target.id
            # Validate the connector family eagerly for a clear startup error.
            _ = target.family
        return self


class TargetCatalog:
    """Thread-safe catalogue automatically reloaded when its file changes."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured_path = path or os.getenv("TARGET_CATALOG_PATH") or _DEFAULT_PATH
        self.path = Path(configured_path)
        self._lock = RLock()
        self._mtime_ns: int | None = None
        self._document: TargetCatalogDocument | None = None

    def reload(self, force: bool = False) -> TargetCatalogDocument:
        with self._lock:
            stat = self.path.stat()
            if (
                not force
                and self._document is not None
                and self._mtime_ns == stat.st_mtime_ns
            ):
                return self._document

            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            self._document = TargetCatalogDocument.model_validate(_interpolate(raw))
            self._mtime_ns = stat.st_mtime_ns
            return self._document

    def targets(self, enabled_only: bool = True) -> list[TargetDefinition]:
        targets = self.reload().targets
        return [target for target in targets if target.enabled] if enabled_only else targets

    def get(self, target_id: str) -> TargetDefinition:
        normalized = target_id.strip().lower()
        for target in self.targets():
            if normalized == target.id or normalized in target.aliases:
                return target
        raise KeyError(f"Unknown or disabled target '{target_id}'")

    def default_for(self, family: TargetService) -> TargetDefinition:
        matches = [target for target in self.targets() if target.family == family]
        if not matches:
            raise KeyError(f"No enabled target configured for family '{family.value}'")
        exact = next((target for target in matches if target.id == family.value.lower()), None)
        return exact or matches[0]

    def resolve(self, value: str | TargetService) -> TargetDefinition:
        if isinstance(value, TargetService):
            return self.default_for(value)
        try:
            return self.get(value)
        except KeyError:
            return self.default_for(TargetService(value.upper()))

    def aliases(self) -> dict[str, TargetDefinition]:
        result: dict[str, TargetDefinition] = {}
        for target in self.targets():
            result[target.id] = target
            for alias in target.aliases:
                result[alias] = target
        return result


target_catalog = TargetCatalog()
