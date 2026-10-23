"""FastAPI dependencies: one SQLite connection per request."""
from __future__ import annotations

import sqlite3
from typing import Iterator

from app.core import db as dbmod


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = dbmod.connect(dbmod.get_db_path())
    # sqlite3 defaults to isolation_level="" (implicit BEGIN before INSERT/UPDATE/
    # DELETE only). Services issue explicit "BEGIN" for read-modify-write
    # transactions, which would clash with an already-implicit transaction.
    conn.isolation_level = None
    try:
        dbmod.init_db(conn)
        yield conn
    finally:
        conn.close()
