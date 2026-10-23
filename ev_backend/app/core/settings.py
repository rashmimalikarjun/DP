"""Typed access to configuration files (apps.json, categories.json, settings.json).

All config lives in ev_backend/app/config/. Nothing here touches the network or DB.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Dict, List

# This file is app/core/settings.py; the package dir is app/ (two levels up),
# config lives at app/config.
CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # .../app
    "config")


def _load(name: str) -> Dict[str, Any]:
    path = os.path.join(CONFIG_DIR, name)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def apps_raw() -> List[Dict[str, Any]]:
    return _load("apps.json")["apps"]


@lru_cache(maxsize=1)
def categories_raw() -> List[Dict[str, Any]]:
    return _load("categories.json")["categories"]


@lru_cache(maxsize=1)
def settings_raw() -> Dict[str, Any]:
    return _load("settings.json")


def reload_config() -> None:
    """Drop cached config (used by tests after editing files)."""
    apps_raw.cache_clear()
    categories_raw.cache_clear()
    settings_raw.cache_clear()


def get_app(app_id: str) -> Dict[str, Any] | None:
    for a in apps_raw():
        if a["id"] == app_id:
            return a
    return None


def applicable_categories(app_id: str) -> List[str]:
    """Categories that can apply to a complaint about the given app."""
    app = get_app(app_id)
    if app is None:
        return [c["id"] for c in categories_raw()]
    cats = app.get("applicable_categories", ["*"])
    all_cats = [c["id"] for c in categories_raw()]
    if "*" in cats:
        return all_cats
    return [c for c in all_cats if c in cats]


def category_ids() -> List[str]:
    return [c["id"] for c in categories_raw()]


def thresholds() -> Dict[str, float]:
    return settings_raw()["thresholds"]


def learning_params() -> Dict[str, Any]:
    return settings_raw()["learning"]


def max_steps() -> int:
    return int(settings_raw().get("max_steps", 8))


def min_expected_gain() -> float:
    return float(settings_raw().get("min_expected_gain", 0.05))


def tool_cost(tool_name: str) -> float:
    return float(settings_raw().get("tool_costs", {}).get(tool_name, 0.10))


def default_seed() -> int:
    return int(settings_raw().get("seed", 42))


def cors_origins() -> List[str]:
    return list(settings_raw().get("cors_origins", ["http://localhost:5173"]))


def log_level() -> str:
    return str(settings_raw().get("log_level", "INFO"))
