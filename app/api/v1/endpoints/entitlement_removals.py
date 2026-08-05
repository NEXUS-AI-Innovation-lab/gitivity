"""Durable approval endpoints for vanished native entitlements."""

import html
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from prisma import Prisma
from pydantic import BaseModel, Field

from app.config.settings import settings
from app.db import get_db
from app.services.entitlement_removal_service import EntitlementRemovalService

router = APIRouter(prefix="/entitlement-removals", tags=["Entitlement removals"])


class EntitlementManifest(BaseModel):
    target_id: str = Field(min_length=1)
    entitlements: list[dict[str, Any]]


class LegacyRole(BaseModel):
    target_id: str
    role_oid: str
    role_name: str
    replacement_key: str


class LegacyMigration(BaseModel):
    roles: list[LegacyRole]
    manifests: list[EntitlementManifest]


@router.post("/reconcile")
async def reconcile(
    manifests: list[EntitlementManifest],
    reconcile_token: Annotated[
        str | None, Header(alias="X-Gateway-Reconcile-Token")
    ] = None,
    db: Prisma = Depends(get_db),
) -> dict[str, Any]:
    """Record complete successful discovery manifests and detect disappearances."""
    if not settings.ENTITLEMENT_RECONCILE_TOKEN:
        raise HTTPException(
            status_code=503, detail="Reconciliation token is not configured"
        )
    if not reconcile_token or not secrets.compare_digest(
        reconcile_token, settings.ENTITLEMENT_RECONCILE_TOKEN
    ):
        raise HTTPException(status_code=403, detail="Invalid reconciliation token")
    service = EntitlementRemovalService(db)
    try:
        return await service.reconcile(
            [manifest.model_dump() for manifest in manifests]
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/legacy")
async def register_legacy(
    migration: LegacyMigration,
    reconcile_token: Annotated[
        str | None, Header(alias="X-Gateway-Reconcile-Token")
    ] = None,
    db: Prisma = Depends(get_db),
) -> dict[str, Any]:
    if (
        not settings.ENTITLEMENT_RECONCILE_TOKEN
        or not reconcile_token
        or not secrets.compare_digest(
            reconcile_token, settings.ENTITLEMENT_RECONCILE_TOKEN
        )
    ):
        raise HTTPException(status_code=403, detail="Invalid reconciliation token")
    result = await EntitlementRemovalService(db).register_legacy_roles(
        [role.model_dump() for role in migration.roles],
        [manifest.model_dump() for manifest in migration.manifests],
    )
    return result


def _page(title: str, message: str, success: bool = True) -> HTMLResponse:
    color = "#16794b" if success else "#a12626"
    return HTMLResponse(
        "<!doctype html><html><body style='font-family:Arial,sans-serif'>"
        f"<h2 style='color:{color}'>{html.escape(title)}</h2>"
        f"<p>{html.escape(message)}</p></body></html>"
    )


@router.get("/decision", response_class=HTMLResponse)
async def decision(
    token: str = Query(...),
    approved: bool = Query(...),
    db: Prisma = Depends(get_db),
) -> HTMLResponse:
    try:
        result = await EntitlementRemovalService(db).decide(token, approved)
    except Exception:
        return _page("Erreur", "La décision n'a pas pu être appliquée.", False)
    status = result["status"]
    if status == "invalid":
        return _page(
            "Lien invalide ou expiré",
            "La demande durable existe toujours. Utilisez le lien de renouvellement reçu par email.",
            False,
        )
    messages = {
        "kept": "Le rôle midPoint sera conservé.",
        "next_level": "La décision est enregistrée et l'approbateur suivant a été notifié.",
        "completed": "Le rôle a été désassigné puis supprimé de midPoint.",
        "cancelled": "L'entitlement est réapparu : la suppression a été annulée.",
    }
    return _page("Décision enregistrée", messages.get(status, status))


@router.get("/renew", response_class=HTMLResponse)
async def renew(token: str = Query(...), db: Prisma = Depends(get_db)) -> HTMLResponse:
    renewed = await EntitlementRemovalService(db).renew(token)
    if not renewed:
        return _page("Lien invalide", "Cette demande n'est plus en attente.", False)
    return _page(
        "Nouveau lien envoyé", "Un nouveau lien sécurisé a été envoyé à l'approbateur."
    )
