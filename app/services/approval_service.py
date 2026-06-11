"""Multi-level email approval chain (replaces the former n8n approval workflow).

Chain state lives in Redis so the consumer process (which starts the chain)
and the API process (which receives the approvers' decisions) stay in sync.
"""
import logging
import secrets
from datetime import datetime, timezone

from app.config.settings import settings
from app.db.repositories.approval_redis_repository import ApprovalRedisRepository
from app.services.email_service import EmailService, email_service
from app.services.email_templates import (
    build_approval_email,
    build_confirmation_email,
)

logger = logging.getLogger(__name__)

WORKER_ID = "gateway-approval-chain"


class ApprovalService:
    """Drives the multi-level approval chain: one email per level, in order."""

    def __init__(
        self,
        approval_repo: ApprovalRedisRepository,
        email_svc: EmailService | None = None,
    ) -> None:
        self._repo = approval_repo
        self._email = email_svc or email_service

    async def start_chain(
        self,
        operation_id: str,
        request_id: str,
        operation_type: str,
        target_service: str,
        user_data: dict,
        approvers: list[dict],
        changes: list[dict] | None = None,
        admin_email: str | None = None,
    ) -> None:
        """Store the chain state and email the first approver.

        Falls back to a single admin_email approver when the list is empty.
        """
        if not approvers:
            fallback = admin_email or settings.ADMIN_APPROVAL_EMAIL
            approvers = [{"email": fallback, "name": "Admin", "level": 1}]

        approvers = sorted(approvers, key=lambda a: a.get("level", 0))

        chain = {
            "operation_id": operation_id,
            "request_id": request_id,
            "operation_type": operation_type,
            "target_service": target_service,
            "user_data": user_data,
            "changes": changes,
            "approvers": approvers,
            "current_index": 0,
            "total": len(approvers),
            "active_token": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        await self._send_level_email(chain)

    async def _send_level_email(self, chain: dict) -> None:
        """Mint a single-use token for the current approver and send the email.

        Persists the updated chain (active_token) before sending so that a
        send failure never leaves a token without chain state.
        """
        index = chain["current_index"]
        approver = chain["approvers"][index]
        token = secrets.token_urlsafe(32)

        chain["active_token"] = token
        await self._repo.store_chain(chain["operation_id"], chain)
        await self._repo.store_decision_token(
            token,
            {
                "operation_id": chain["operation_id"],
                "approver_email": approver["email"],
                "approver_name": approver["name"],
                "level": approver["level"],
                "level_index": index,
            },
        )

        base_url = f"{settings.GATEWAY_EXTERNAL_URL}/api/v1/approvals/decision?token={token}"
        subject, html = build_approval_email(
            operation_id=chain["operation_id"],
            operation_type=chain["operation_type"],
            target_service=chain["target_service"],
            user_data=chain["user_data"],
            approver_name=approver["name"],
            approver_level=approver["level"],
            total_approvers=chain["total"],
            approve_url=f"{base_url}&approved=true",
            reject_url=f"{base_url}&approved=false",
            changes=chain.get("changes"),
        )

        await self._email.send_html(approver["email"], subject, html)
        logger.info(
            f"Approval email sent to {approver['email']} "
            f"(level {approver['level']}/{chain['total']}) "
            f"for operation {chain['operation_id']}"
        )

    async def handle_decision(self, token: str, approved: bool, orchestrator) -> dict:
        """Process an approver's click on an APPROUVER/REJETER link.

        Args:
            token: Single-use decision token from the email link
            approved: The decision
            orchestrator: ProvisioningOrchestrator used for terminal decisions

        Returns:
            {"status": "invalid" | "rejected" | "next_level" | "approved",
             "approver_name", "level", "total"} (keys present when applicable)
        """
        token_data = await self._repo.consume_decision_token(token)
        if not token_data:
            return {"status": "invalid"}

        operation_id = token_data["operation_id"]
        approver_name = token_data["approver_name"]
        level = token_data["level"]

        chain = await self._repo.get_chain(operation_id)
        if not chain or chain.get("active_token") != token:
            # Chain expired, or this token belongs to a superseded level
            return {"status": "invalid"}

        if not approved:
            reason = f"Rejected by {approver_name} (Level {level})"
            await self._cleanup(operation_id, chain)
            await orchestrator.process_approval_response(
                operation_id=operation_id,
                approved=False,
                reason=reason,
                worker_id=WORKER_ID,
            )
            return {
                "status": "rejected",
                "approver_name": approver_name,
                "level": level,
                "total": chain["total"],
            }

        next_index = chain["current_index"] + 1
        if next_index < chain["total"]:
            chain["current_index"] = next_index
            await self._send_level_email(chain)
            return {
                "status": "next_level",
                "approver_name": approver_name,
                "level": level,
                "total": chain["total"],
            }

        # Last level approved -> provision, then confirmation email
        await self._cleanup(operation_id, chain)
        await orchestrator.process_approval_response(
            operation_id=operation_id,
            approved=True,
            reason="Approved by all approvers",
            worker_id=WORKER_ID,
        )
        await self._send_confirmation_email(chain)
        return {
            "status": "approved",
            "approver_name": approver_name,
            "level": level,
            "total": chain["total"],
        }

    async def _cleanup(self, operation_id: str, chain: dict) -> None:
        """Remove chain state after a terminal decision (tokens are already consumed)."""
        await self._repo.delete_chain(operation_id)
        active_token = chain.get("active_token")
        if active_token:
            await self._repo.delete_decision_token(active_token)

    async def _send_confirmation_email(self, chain: dict) -> None:
        """Send the confirmation/credentials email to the end user.

        Only after provisioning succeeded; an SMTP failure must never fail
        the operation, so errors are logged and swallowed.
        """
        try:
            to, subject, html = build_confirmation_email(
                operation_type=chain["operation_type"],
                target_service=chain["target_service"],
                user_data=chain["user_data"],
            )
            if not to or to == "N/A":
                logger.info(
                    f"No user email for operation {chain['operation_id']}, "
                    "skipping confirmation email"
                )
                return
            await self._email.send_html(to, subject, html)
        except Exception as e:
            logger.error(
                f"Failed to send confirmation email for operation "
                f"{chain['operation_id']}: {e}"
            )
