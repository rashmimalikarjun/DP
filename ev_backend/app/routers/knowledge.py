from __future__ import annotations

from fastapi import APIRouter, Depends

from app.schemas import KnowledgeRuleOut, ToolPriorOut, KnowledgeChangeOut, RollbackRequest
from app.routers.deps import get_conn
from app.routers.errors import ApiHTTP
from app.services import feedback

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/rules", response_model=list[KnowledgeRuleOut])
def rules(conn=Depends(get_conn)):
    rows = conn.execute("SELECT * FROM knowledge_rules ORDER BY rule_id").fetchall()
    return [{**dict(r), "active": bool(r["active"])} for r in rows]


@router.get("/tool-priors", response_model=list[ToolPriorOut])
def priors(conn=Depends(get_conn)):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM tool_priors ORDER BY tool_name, category").fetchall()]


@router.get("/changes", response_model=list[KnowledgeChangeOut])
def changes(conn=Depends(get_conn)):
    out = []
    for c in feedback.list_changes(conn):
        c = dict(c)
        c["reverted"] = bool(c["reverted"])
        out.append(c)
    return out


@router.post("/changes/{change_id}/revert")
def revert(change_id: int, conn=Depends(get_conn)):
    try:
        return feedback.revert_change(conn, change_id)
    except feedback.FeedbackError as exc:
        code = {"change_not_found": 404, "already_reverted": 409}.get(exc.code, 400)
        raise ApiHTTP(code, exc.code, exc.message, exc.details)


@router.post("/rollback")
def rollback(req: RollbackRequest, conn=Depends(get_conn)):
    try:
        return feedback.rollback_to(conn, req.to_change_id, req.reason)
    except feedback.FeedbackError as exc:
        raise ApiHTTP(404 if exc.code == "change_not_found" else 400,
                      exc.code, exc.message, exc.details)
