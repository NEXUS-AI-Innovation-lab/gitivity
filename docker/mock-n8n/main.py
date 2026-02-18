"""
Mock n8n Service - Simulates n8n validation and notification webhooks.

Modes:
- auto-approve: Always approves validation requests
- auto-reject: Always rejects validation requests
- random: Randomly approves/rejects based on MOCK_REJECT_RATE
- delay: Adds configurable delay before responding
"""

import asyncio
import os
import random
import uuid
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(
    title="n8n Mock Service",
    description="Simulates n8n webhooks for Gateway IAM testing",
    version="1.0.0",
)

# Configuration from environment
MOCK_MODE = os.getenv("MOCK_MODE", "auto-approve")
MOCK_DELAY_MS = int(os.getenv("MOCK_DELAY_MS", "0"))
MOCK_REJECT_RATE = float(os.getenv("MOCK_REJECT_RATE", "0.3"))


class ValidationRequest(BaseModel):
    """Incoming validation request from Gateway IAM."""
    operation_id: str
    request_id: str
    target_service: str
    operation_type: str
    user_data: dict[str, Any]
    metadata: dict[str, Any] | None = None


class ValidationResponse(BaseModel):
    """Validation response sent back to Gateway IAM."""
    approved: bool
    validation_id: str
    reason: str | None = None
    validated_at: str
    validator: str = "n8n-mock"


class NotificationRequest(BaseModel):
    """Incoming notification request from Gateway IAM."""
    operation_id: str
    request_id: str | None = None
    target_service: str
    operation_type: str
    status: str
    user_data: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class NotificationResponse(BaseModel):
    """Notification response sent back to Gateway IAM."""
    status: str
    notification_id: str
    sent_at: str


# Storage for requests (for debugging/inspection)
validation_requests: list[dict] = []
notification_requests: list[dict] = []


async def apply_delay():
    """Apply configured delay if in delay mode."""
    if MOCK_MODE == "delay" and MOCK_DELAY_MS > 0:
        await asyncio.sleep(MOCK_DELAY_MS / 1000)


def should_approve() -> tuple[bool, str | None]:
    """Determine if request should be approved based on mode."""
    if MOCK_MODE == "auto-approve":
        return True, None
    elif MOCK_MODE == "auto-reject":
        return False, "Rejected by mock (auto-reject mode)"
    elif MOCK_MODE == "random":
        approved = random.random() > MOCK_REJECT_RATE
        reason = None if approved else "Randomly rejected by mock"
        return approved, reason
    elif MOCK_MODE == "delay":
        return True, None
    else:
        return True, None


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "n8n-mock",
        "mode": MOCK_MODE,
        "delay_ms": MOCK_DELAY_MS,
        "reject_rate": MOCK_REJECT_RATE if MOCK_MODE == "random" else None,
    }


@app.post("/webhook/validation", response_model=ValidationResponse)
async def validate_request(
    request: ValidationRequest,
    force_approve: bool = Query(False, description="Force approval regardless of mode"),
    force_reject: bool = Query(False, description="Force rejection regardless of mode"),
):
    """
    Validation webhook - simulates n8n pre-provisioning validation.

    Use query params to override behavior:
    - ?force_approve=true - Force approval
    - ?force_reject=true - Force rejection
    """
    await apply_delay()

    # Store request for inspection
    validation_requests.append({
        "timestamp": datetime.utcnow().isoformat(),
        "request": request.model_dump(),
    })

    # Determine approval
    if force_reject:
        approved, reason = False, "Forced rejection via query param"
    elif force_approve:
        approved, reason = True, None
    else:
        approved, reason = should_approve()

    return ValidationResponse(
        approved=approved,
        validation_id=str(uuid.uuid4()),
        reason=reason,
        validated_at=datetime.utcnow().isoformat(),
    )


@app.post("/webhook/notification", response_model=NotificationResponse)
async def send_notification(request: NotificationRequest):
    """
    Notification webhook - simulates n8n post-provisioning notification.

    Always returns success (notifications are fire-and-forget).
    """
    await apply_delay()

    # Store request for inspection
    notification_requests.append({
        "timestamp": datetime.utcnow().isoformat(),
        "request": request.model_dump(),
    })

    return NotificationResponse(
        status="sent",
        notification_id=str(uuid.uuid4()),
        sent_at=datetime.utcnow().isoformat(),
    )


@app.get("/debug/validations")
async def get_validation_requests():
    """Get all validation requests received (for debugging)."""
    return {
        "count": len(validation_requests),
        "requests": validation_requests[-50:],  # Last 50
    }


@app.get("/debug/notifications")
async def get_notification_requests():
    """Get all notification requests received (for debugging)."""
    return {
        "count": len(notification_requests),
        "requests": notification_requests[-50:],  # Last 50
    }


@app.delete("/debug/clear")
async def clear_requests():
    """Clear all stored requests."""
    validation_requests.clear()
    notification_requests.clear()
    return {"status": "cleared"}


@app.put("/config/mode")
async def set_mode(
    mode: str = Query(..., description="Mode: auto-approve, auto-reject, random, delay"),
):
    """Change mock mode at runtime."""
    global MOCK_MODE
    if mode not in ["auto-approve", "auto-reject", "random", "delay"]:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {mode}")
    MOCK_MODE = mode
    return {"status": "updated", "mode": MOCK_MODE}


@app.put("/config/delay")
async def set_delay(delay_ms: int = Query(..., ge=0, description="Delay in milliseconds")):
    """Change delay at runtime."""
    global MOCK_DELAY_MS
    MOCK_DELAY_MS = delay_ms
    return {"status": "updated", "delay_ms": MOCK_DELAY_MS}


@app.put("/config/reject-rate")
async def set_reject_rate(rate: float = Query(..., ge=0, le=1, description="Rejection rate (0-1)")):
    """Change rejection rate at runtime."""
    global MOCK_REJECT_RATE
    MOCK_REJECT_RATE = rate
    return {"status": "updated", "reject_rate": MOCK_REJECT_RATE}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
