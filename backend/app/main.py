"""CircuitSense FastAPI application."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.api.errors import install_error_handlers
from app.api.routes import VERSION, router
from app.config import Settings, apply_bedrock_api_key_from_dotenv, get_settings
from app.services.container import Services, build_services

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    if settings is None:
        key_source = apply_bedrock_api_key_from_dotenv()
        if key_source:
            logging.getLogger(__name__).info("Amazon Bedrock API key loaded from %s", key_source)
    settings = settings or get_settings()
    services = services or build_services(settings)
    app = FastAPI(
        title="CircuitSense API",
        version=VERSION,
        description="Evidence-based circuit analysis and debugging agent.",
    )
    app.state.services = services
    install_error_handlers(app)

    request_limit = settings.max_upload_bytes * 2 + 1_048_576

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > request_limit:
            return JSONResponse(
                status_code=413,
                content={"error": {"code": "payload_too_large", "message": f"Request body exceeds {request_limit // 1_048_576} MB."}},
            )
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.include_router(router)

    dist = settings.frontend_dist
    if dist and Path(dist).is_dir():
        dist_path = Path(dist).resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path == "api" or full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": "Not Found"}})
            candidate = (dist_path / full_path).resolve()
            if full_path and candidate.is_file() and dist_path in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist_path / "index.html")

    return app


app = create_app()
