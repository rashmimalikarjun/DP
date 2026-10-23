from __future__ import annotations

from fastapi import APIRouter, Depends

from app.schemas import LearningResult, RollbackRequest
from app.routers.deps import get_conn
from app.routers.errors import ApiHTTP
from app.services import feedback

router = APIRouter(tags=["feedback"])

_MAP = {"no_resolution": 409, "resolution_not_found": 404, "change_not_found": 404,
        "already_reverted": 409, "unrevertable": 400, "case_not_found": 404}


@router.post("/cases/{case_id}/learning/preview", response_model=LearningResult)
def preview(case_id: str, conn=Depends(get_conn)):
    try:
        return feedback.preview_learning(conn, case_id)
    except feedback.FeedbackError as exc:
        raise ApiHTTP(_MAP.get(exc.code, 400), exc.code, exc.message, exc.details)


@router.post("/cases/{case_id}/learning/apply", response_model=LearningResult)
def apply_learning(case_id: str, conn=Depends(get_conn)):
    try:
        res = conn.execute("SELECT id FROM resolutions WHERE case_id=?", (case_id,)).fetchone()
        if res is None:
            raise feedback.FeedbackError("no_resolution",
                                         f"Case {case_id} has no verified resolution.")
        return feedback.apply_verified_outcome(conn, res["id"])
    except feedback.FeedbackError as exc:
        raise ApiHTTP(_MAP.get(exc.code, 400), exc.code, exc.message, exc.details)
