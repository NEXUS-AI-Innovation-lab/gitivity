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
_DEFAULT_RUNTIME_PATH = Path(__file__).resolve().parents[2] / "data" / "runtime-targets.yaml"


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
    """Native entitlement discovery and MidPoint association metadata."""

    provider: str
    identifier: str = "{name}"
    association_ref: str
    base_name: str = "gateway-base"


class TargetDeployment(BaseModel):
    """Deployment-only values used by Ansible artifact generation."""

    environment: dict[str, Any] = Field(default_factory=dict)


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
    deployment: TargetDeployment = Field(default_factory=TargetDeployment)

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
    """Thread-safe merged persistent/runtime catalogue with hot reload."""

    def __init__(
        self,
        path: str | Path | None = None,
        runtime_path: str | Path | None = None,
    ) -> None:
        configured_path = path or os.getenv("TARGET_CATALOG_PATH") or _DEFAULT_PATH
        self.path = Path(configured_path)
        configured_runtime_path = runtime_path
        if path is None and runtime_path is None:
            configured_runtime_path = (
                os.getenv("RUNTIME_TARGET_CATALOG_PATH") or _DEFAULT_RUNTIME_PATH
            )
        self.runtime_path = (
            Path(configured_runtime_path) if configured_runtime_path is not None else None
        )
        self._lock = RLock()
        self._mtimes: tuple[int, int | None] | None = None
        self._document: TargetCatalogDocument | None = None
        self._persistent_ids: set[str] = set()

    def _runtime_mtime(self) -> int | None:
        if self.runtime_path is None or not self.runtime_path.exists():
            return None
        return self.runtime_path.stat().st_mtime_ns

    def _read_document(self, path: Path) -> TargetCatalogDocument:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw.setdefault("targets", [])
        return TargetCatalogDocument.model_validate(_interpolate(raw))

    def reload(self, force: bool = False) -> TargetCatalogDocument:
        with self._lock:
            mtimes = (self.path.stat().st_mtime_ns, self._runtime_mtime())
            if (
                not force
                and self._document is not None
                and self._mtimes == mtimes
            ):
                return self._document

            persistent = self._read_document(self.path)
            self._persistent_ids = {target.id for target in persistent.targets}
            runtime_targets: list[TargetDefinition] = []
            if self.runtime_path is not None and self.runtime_path.exists():
                runtime_targets = [
                    target
                    for target in self._read_document(self.runtime_path).targets
                    if target.id not in self._persistent_ids
                ]

            self._document = TargetCatalogDocument(
                version=persistent.version,
                targets=[*persistent.targets, *runtime_targets],
            )
            self._mtimes = mtimes
            return self._document

    def source(self, target_id: str) -> str:
        target = self.get(target_id)
        return "persistent" if target.id in self._persistent_ids else "runtime"

    def add_runtime(self, target: TargetDefinition) -> TargetDefinition:
        """Persist a tested target in the runtime overlay."""
        if self.runtime_path is None:
            raise ValueError("Runtime target catalogue is disabled")

        with self._lock:
            document = self.reload(force=True)
            identifiers = {
                identifier
                for existing in document.targets
                for identifier in [existing.id, *existing.aliases]
            }
            requested = {target.id, *target.aliases}
            conflicts = sorted(identifiers & requested)
            if conflicts:
                raise ValueError(
                    f"Target identifiers already exist: {', '.join(conflicts)}"
                )

            runtime_targets = [
                existing
                for existing in document.targets
                if existing.id not in self._persistent_ids
            ]
            runtime_document = TargetCatalogDocument(
                version=document.version,
                targets=[*runtime_targets, target],
            )
            self.runtime_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.runtime_path.with_suffix(
                f"{self.runtime_path.suffix}.tmp"
            )
            temporary_path.write_text(
                yaml.safe_dump(
                    runtime_document.model_dump(mode="json", exclude_defaults=True),
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            temporary_path.replace(self.runtime_path)
            self.reload(force=True)
            return self.get(target.id)

    def remove_runtime(self, target_id: str) -> None:
        """Remove a target from the runtime overlay only."""
        if self.runtime_path is None:
            raise ValueError("Runtime target catalogue is disabled")

        with self._lock:
            target = self.get(target_id)
            if target.id in self._persistent_ids:
                raise ValueError("Persistent targets cannot be removed at runtime")
            runtime_targets = [
                existing
                for existing in self.reload().targets
                if existing.id not in self._persistent_ids and existing.id != target.id
            ]
            self.runtime_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.runtime_path.with_suffix(
                f"{self.runtime_path.suffix}.tmp"
            )
            temporary_path.write_text(
                yaml.safe_dump(
                    TargetCatalogDocument(
                        version=1,
                        targets=runtime_targets,
                    ).model_dump(mode="json", exclude_defaults=True),
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            temporary_path.replace(self.runtime_path)
            self.reload(force=True)

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
