from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


@dataclass
class BusinessError(Exception):
    code: str
    message: str
    status_code: int = 400
    details: dict[str, Any] | None = None


async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={
        "error": {
            "code": exc.code,
            "message": exc.message,
            "details": exc.details or {},
            "request_id": getattr(request.state, "request_id", None),
        }
    })


async def unexpected_error_handler(request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "服务暂时无法完成请求，请稍后重试。",
            "details": {},
            "request_id": getattr(request.state, "request_id", None),
        }
    })
