"""Logging setup: console + rotating file under logs/ (git-ignored).

No PII is ever logged: callers must pass already-redacted text.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from app.core.settings import log_level

LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "logs")

_configured = False


def setup_logging(level: str | None = None) -> None:
    global _configured
    if _configured:
        return
    lvl = getattr(logging, (level or log_level()).upper(), logging.INFO)
    root = logging.getLogger("app")
    root.setLevel(lvl)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        fh = RotatingFileHandler(
            os.path.join(LOG_DIR, "ev_backend.log"),
            maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:  # pragma: no cover - e.g. read-only FS in tests
        pass

    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
