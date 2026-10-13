from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.api.errors import (
    BusinessError, business_error_handler, unexpected_error_handler,
)
from backend.app.api.routes import router
from backend.app.config import Settings, get_settings, sqlite_path
from backend.app.db.session import create_session_factory, create_sqlite_engine


def create_app(*, settings: Settings | None = None, session_factory=None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="储蓄目标个性化预算系统",
        version="0.4.0",
        description="周度消费预测、目标可行性判断和滚动预算再规划API",
    )
    app.state.settings = settings
    if session_factory is None:
        engine = create_sqlite_engine(sqlite_path(settings.database_url))
        session_factory = create_session_factory(engine)
        app.state.engine = engine
    app.state.session_factory = session_factory
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins),
        allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {
            "code": "VALIDATION_ERROR", "message": "请求参数不符合要求。",
            "details": {"issues": exc.errors()},
            "request_id": getattr(request.state, "request_id", None),
        }})

    app.add_exception_handler(BusinessError, business_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    app.include_router(router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
