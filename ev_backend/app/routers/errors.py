"""Shared error-raising helpers so every router returns the same shape."""
from __future__ import annotations

from fastapi import HTTPException


class ApiHTTP(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, details=None):
        super().__init__(status_code=status_code,
                         detail={"code": code, "message": message, "details": details})
