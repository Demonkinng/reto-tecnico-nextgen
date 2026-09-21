import asyncio
import logging
import re
import traceback
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import Settings
from .schemas import RecommendationRequest, RecommendationResponse
from .service import RULE_VERSION, recommend
from .telemetry import Telemetry


def create_app(settings=None):
    settings = settings or Settings.from_env()
    telemetry = Telemetry()
    application = FastAPI(title="SmartBancs — IA simulada", version="0.1.0",
                          description="Mock funcional basado en reglas. No utiliza un modelo entrenado.")

    def error_response(request, status, code, message):
        return JSONResponse(status_code=status, content={
            "error": {"code": code, "message": message}, "trace_id": request.state.trace_id,
        })

    @application.middleware("http")
    async def observe_request(request, call_next):
        supplied = request.headers.get("X-Trace-ID", "")
        trace_id = supplied if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", supplied) else str(uuid4())
        request.state.trace_id = trace_id
        started = perf_counter()
        status = 500
        try:
            try:
                response = await call_next(request)
            except Exception as error:
                frame = traceback.extract_tb(error.__traceback__)[-1]
                telemetry.log("unexpected_error", trace_id, level=logging.ERROR,
                              exception_type=type(error).__name__, function=frame.name, line=frame.lineno)
                if request.method == "POST" and request.url.path == "/recommendations":
                    telemetry.inferences.labels("error").inc()
                response = error_response(request, 500, "internal_error", "No se pudo generar la recomendación")
            status = response.status_code
            response.headers["X-Trace-ID"] = trace_id
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            method = request.method if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else "OTHER"
            if route != "/metrics":
                elapsed = perf_counter() - started
                telemetry.requests.labels(method, route, str(status)).inc()
                telemetry.duration.labels(method, route).observe(elapsed)
                telemetry.log("http_request", trace_id, method=method, route=route,
                              status=status, duration_ms=round(elapsed * 1000, 2))

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        telemetry.log("validation_failed", request.state.trace_id, error_count=len(error.errors()))
        return error_response(request, 422, "invalid_request", "Revisa los datos de la transacción")

    @application.post("/recommendations", response_model=RecommendationResponse,
                      responses={503: {"description": "Indisponibilidad simulada"}})
    async def post_recommendation(body: RecommendationRequest, request: Request):
        trace_id = request.state.trace_id
        telemetry.log("recommendation_started", trace_id, transaction_id=body.transaction_id,
                      rule_version=RULE_VERSION)
        if settings.delay_seconds:
            # La demora artificial cede el control para no bloquear /health.
            await asyncio.sleep(settings.delay_seconds)
        if settings.failure_mode == "unavailable":
            telemetry.inferences.labels("simulated_failure").inc()
            telemetry.log("recommendation_unavailable", trace_id, level=logging.WARNING,
                          transaction_id=body.transaction_id)
            return error_response(request, 503, "mock_unavailable", "Indisponibilidad simulada de IA")
        result = recommend(body)
        telemetry.inferences.labels("success").inc()
        telemetry.log("recommendation_generated", trace_id, transaction_id=body.transaction_id,
                      rule_version=RULE_VERSION)
        return result

    @application.get("/health")
    async def health():
        return {"status": "ok", "mode": "mock", "rule_version": RULE_VERSION}

    @application.get("/ready")
    async def ready(request: Request):
        if settings.failure_mode == "unavailable":
            return error_response(request, 503, "mock_unavailable", "Indisponibilidad simulada de IA")
        return {"status": "ready", "mode": "mock"}

    @application.get("/metrics", include_in_schema=False)
    async def metrics():
        return Response(generate_latest(telemetry.registry), media_type=CONTENT_TYPE_LATEST)

    return application


app = create_app()
