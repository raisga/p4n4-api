"""Audit log (admin): who changed what, and when."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from p4n4_api import audit, db

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEntry(BaseModel):
    id: int
    at: str
    actor: str  # username, or "anonymous" with auth off
    action: str  # e.g. stack.up, user.create, device.rotate_key
    target: str
    outcome: str
    request_id: str  # matches the X-Request-ID of the request that caused it


class AuditPage(BaseModel):
    items: list[AuditEntry]
    total: int
    limit: int
    offset: int


@router.get("")
def list_audit(
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPage:
    """Newest first."""
    with db.connect() as conn:
        items, total = audit.page(conn, limit, offset)
    return AuditPage(items=items, total=total, limit=limit, offset=offset)
