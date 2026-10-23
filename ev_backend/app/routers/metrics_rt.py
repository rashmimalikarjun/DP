from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.routers.deps import get_conn
from app.services.metrics import performance_metrics, recurring_problems

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/performance")
def performance(conn=Depends(get_conn), app_id: Optional[str] = None,
                data_origin: Optional[str] = None, date_from: Optional[str] = None,
                date_to: Optional[str] = None):
    return performance_metrics(conn, app_id=app_id, data_origin=data_origin,
                               date_from=date_from, date_to=date_to)


@router.get("/recurring")
def recurring(conn=Depends(get_conn), min_count: int = Query(2, ge=1)):
    return recurring_problems(conn, min_count=min_count)
