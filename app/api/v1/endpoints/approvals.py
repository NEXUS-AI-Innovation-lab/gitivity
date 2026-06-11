"""Approval decision endpoint (links clicked from the approval emails)"""
import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from prisma import Prisma

from app.core.orchestrator import ProvisioningOrchestrator
from app.db import get_db
from app.db.redis_client import RedisClient
from app.db.repositories.approval_redis_repository import ApprovalRedisRepository
from app.services.approval_service import ApprovalService
from app.services.email_templates import build_decision_result_page

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/approvals", tags=["Approvals"])


@router.get("/decision", response_class=HTMLResponse)
async def approval_decision(
    token: str = Query(..., description="Single-use decision token from the email"),
    approved: bool = Query(..., description="Approver decision"),
    db: Prisma = Depends(get_db),
) -> HTMLResponse:
    """Record an approver's decision (APPROUVER / REJETER link in the email).

    GET because email clients only emit GETs from links. The single-use token
    (consumed atomically in Redis) makes replay or double-click harmless.
    """
    redis_client = await RedisClient.get_client()
    approval_repo = ApprovalRedisRepository(redis_client)
    approval_service = ApprovalService(approval_repo)
    orchestrator = ProvisioningOrchestrator(db)

    try:
        result = await approval_service.handle_decision(token, approved, orchestrator)
    except Exception as e:
        logger.exception(f"Failed to process approval decision: {e}")
        return HTMLResponse(
            content=build_decision_result_page(
                "Erreur",
                "Une erreur est survenue lors du traitement de votre decision. "
                "Contactez l'administrateur.",
                success=False,
            ),
            status_code=500,
        )

    status = result["status"]

    if status == "invalid":
        return HTMLResponse(
            content=build_decision_result_page(
                "Lien invalide",
                "Lien invalide ou deja utilise. Cette demande a peut-etre deja "
                "ete traitee ou a expire.",
                success=False,
            ),
            status_code=410,
        )

    if status == "rejected":
        return HTMLResponse(
            content=build_decision_result_page(
                "Demande rejetee",
                f"Vous avez rejete cette demande (niveau {result['level']}). "
                "Le provisioning a ete annule et l'operation marquee comme rejetee.",
                success=False,
            )
        )

    if status == "next_level":
        return HTMLResponse(
            content=build_decision_result_page(
                "Approbation enregistree",
                f"Approbation niveau {result['level']}/{result['total']} enregistree. "
                "L'approbateur suivant a ete notifie par email.",
            )
        )

    # status == "approved"
    return HTMLResponse(
        content=build_decision_result_page(
            "Demande approuvee",
            "Tous les niveaux ont approuve cette demande. "
            "Le provisioning a ete lance avec succes.",
        )
    )
