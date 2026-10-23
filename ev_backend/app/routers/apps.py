from __future__ import annotations

from fastapi import APIRouter
from app.core import settings as cfg
from app.schemas import AppInfo, CategoryInfo

router = APIRouter(tags=["config"])


@router.get("/apps", response_model=list[AppInfo])
def list_apps():
    out = []
    for a in cfg.apps_raw():
        out.append({"id": a["id"], "display_name": a["display_name"],
                    "app_type": a["app_type"],
                    "applicable_categories": cfg.applicable_categories(a["id"])})
    return out


@router.get("/categories", response_model=list[CategoryInfo])
def list_categories():
    return [{"id": c["id"], "label": c["label"], "description": c["description"],
             "example_signals": c.get("example_signals", [])}
            for c in cfg.categories_raw()]
