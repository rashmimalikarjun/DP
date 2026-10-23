from __future__ import annotations

from fastapi import APIRouter, Depends

from app.routers.deps import get_conn
from app.schemas import ReviewerOut

router = APIRouter(tags=["reviewers"])


@router.get("/reviewers", response_model=list[ReviewerOut])
def list_reviewers(conn=Depends(get_conn)):
    rows = conn.execute("SELECT * FROM reviewers ORDER BY reviewer_id").fetchall()
    return [{**dict(r), "active": bool(r["active"])} for r in rows]
