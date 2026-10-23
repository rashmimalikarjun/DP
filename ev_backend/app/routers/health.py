from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from app.schemas import HealthOut
from app.core.db import query_one
from app.routers.deps import get_conn

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health(conn=Depends(get_conn)):
    try:
        v = query_one(conn, "SELECT MAX(version) AS v FROM schema_version")
        ok, ver = True, (v["v"] if v else None)
    except Exception:
        ok, ver = False, None
    return {"status": "ok" if ok else "degraded", "db_ok": ok, "schema_version": ver,
            "time": datetime.now(timezone.utc).isoformat()}
