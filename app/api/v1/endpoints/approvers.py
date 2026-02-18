"""API endpoints for approver management"""
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/approvers", tags=["approvers"])

DATA_FILE = Path(__file__).resolve().parents[4] / "data" / "approvers.json"


# ============================================================================
# Models
# ============================================================================

class ApproverCreate(BaseModel):
    name: str
    email: str
    level: int


class ApproverUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    level: int | None = None


class ApproverResponse(BaseModel):
    id: str
    name: str
    email: str
    level: int


# ============================================================================
# Helpers
# ============================================================================

def _read_approvers() -> list[dict]:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("approvers", [])
    except FileNotFoundError:
        return []


def _write_approvers(approvers: list[dict]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"approvers": approvers}, f, indent=2, ensure_ascii=False)


# ============================================================================
# Endpoints
# ============================================================================

@router.get("", response_model=list[ApproverResponse])
async def list_approvers():
    """List all approvers sorted by level"""
    approvers = _read_approvers()
    return sorted(approvers, key=lambda a: a.get("level", 0))


@router.post("", response_model=ApproverResponse, status_code=status.HTTP_201_CREATED)
async def create_approver(data: ApproverCreate):
    """Add a new approver"""
    approvers = _read_approvers()

    approver = {
        "id": str(uuid.uuid4())[:8],
        "name": data.name,
        "email": data.email,
        "level": data.level,
    }

    approvers.append(approver)
    _write_approvers(approvers)
    logger.info(f"Created approver: {approver['name']} (level {approver['level']})")
    return approver


@router.put("/{approver_id}", response_model=ApproverResponse)
async def update_approver(approver_id: str, data: ApproverUpdate):
    """Update an existing approver"""
    approvers = _read_approvers()

    for approver in approvers:
        if approver["id"] == approver_id:
            if data.name is not None:
                approver["name"] = data.name
            if data.email is not None:
                approver["email"] = data.email
            if data.level is not None:
                approver["level"] = data.level
            _write_approvers(approvers)
            logger.info(f"Updated approver: {approver['name']}")
            return approver

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Approver '{approver_id}' not found",
    )


@router.delete("/{approver_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_approver(approver_id: str):
    """Delete an approver"""
    approvers = _read_approvers()
    original_len = len(approvers)
    approvers = [a for a in approvers if a["id"] != approver_id]

    if len(approvers) == original_len:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approver '{approver_id}' not found",
        )

    _write_approvers(approvers)
    logger.info(f"Deleted approver: {approver_id}")
