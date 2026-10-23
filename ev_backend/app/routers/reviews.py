from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.core.settings import get_app
from app.redaction import redact_pii
from app.routers.deps import get_conn
from app.routers.errors import ApiHTTP
from app.schemas import ReviewImportResult, ReviewOut

router = APIRouter(prefix="/reviews", tags=["reviews"])

MAX_BYTES = 2 * 1024 * 1024  # 2 MB upload cap


def _insert_review(conn, row: dict, errors: list) -> int:
    app_id = (row.get("app_id") or "").strip()
    text = (row.get("text") or row.get("original_text") or "").strip()
    origin = (row.get("data_origin") or "").strip()
    if not text:
        errors.append(f"row skipped (empty text): {str(row)[:80]}")
        return 0
    if get_app(app_id) is None:
        errors.append(f"row skipped (unknown app_id '{app_id}')")
        return 0
    if origin not in ("real_public_review", "simulated", "manual_test"):
        errors.append(f"row skipped (invalid data_origin '{origin}')")
        return 0
    conn.execute(
        "INSERT INTO reviews(app_id, source, source_ref, review_date, original_text,"
        " redacted_text, problem_category, data_origin) VALUES (?,?,?,?,?,?,?,?)",
        (app_id, (row.get("source") or "user_import").strip(), row.get("source_ref"),
         row.get("review_date"), text, redact_pii(text),
         row.get("problem_category"), origin))
    return 1


@router.post("/import", response_model=ReviewImportResult, summary="Import legally collected reviews (CSV or JSON)")
async def import_reviews(file: UploadFile = File(...), conn=Depends(get_conn)):
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise ApiHTTP(400, "upload_too_large", "Upload exceeds 2 MB limit.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ApiHTTP(400, "encoding_error", "File must be UTF-8 text (CSV or JSON).")
    imported = skipped = 0
    errors: list[str] = []
    try:
        if file.filename and file.filename.endswith(".json") or text.lstrip().startswith(("[", "{")):
            data = json.loads(text)
            rows = data if isinstance(data, list) else data.get("reviews", [])
            for r in rows:
                n = _insert_review(conn, r, errors)
                imported += n
                skipped += 1 - n
        else:
            reader = csv.DictReader(io.StringIO(text))
            required = {"app_id", "text"}
            if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
                raise ApiHTTP(400, "bad_csv",
                              "CSV must have at least columns: app_id, text "
                              "(optional: source, source_ref, review_date, problem_category, data_origin)")
            for r in reader:
                n = _insert_review(conn, r, errors)
                imported += n
                skipped += 1 - n
        conn.commit()
    except ApiHTTP:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise ApiHTTP(400, "parse_error", "Could not parse upload.", {"reason": str(exc)[:200]})
    return {"imported": imported, "skipped": skipped, "errors": errors[:50]}


@router.get("", response_model=list[ReviewOut])
def list_reviews(conn=Depends(get_conn), app_id: str | None = None,
                 data_origin: str | None = None, limit: int = 100):
    q, params = ["SELECT * FROM reviews WHERE 1=1"], []
    if app_id:
        q.append("AND app_id=?"); params.append(app_id)
    if data_origin:
        q.append("AND data_origin=?"); params.append(data_origin)
    q.append("ORDER BY review_id DESC LIMIT ?"); params.append(min(limit, 500))
    return [dict(r) for r in conn.execute(" ".join(q), tuple(params)).fetchall()]
