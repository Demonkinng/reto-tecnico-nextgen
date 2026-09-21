import logging
import re
import traceback
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Annotated
from uuid import UUID, uuid4

import psycopg
from fastapi import FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from psycopg_pool import PoolTimeout, TooManyRequests

from .config import Settings
from .database import Database
from .schemas import AccountResponse, RecommendationResponse, TransferRequest, TransferResponse
from .service import ServiceError, create_transfer, get_account, get_recommendation, get_transfer
from .telemetry import Telemetry, error_kind, trace_id_context

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]


def create_app(settings=None):
    telemetry = Telemetry()

    @asynccontextmanager
    async def lifespan(application):
        database = Database(settings or Settings.from_env(), telemetry)
        application.state.database = database
        try:
            database.open()
            yield
        finally:
            database.close()

    application = FastAPI(title="SmartBancs — Transacciones", version="0.1.0", lifespan=lifespan)

    @application.middleware("http")
    async def observe_request(request, call_next):
        supplied = request.headers.get("X-Trace-ID", "")
        trace_id = supplied if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", supplied) else str(uuid4())
        token = trace_id_context.set(trace_id)
        request.state.trace_id = trace_id
        started = perf_counter()
        status = 500
        try:
            try:
                response = await call_next(request)
            except Exception as error:
                frame = traceback.extract_tb(error.__traceback__)[-1]
                telemetry.log("unexpected_error", level=logging.ERROR,
                              exception_type=type(error).__name__,
                              function=frame.name, line=frame.lineno)
                response = error_response(request, 500, "internal_error", "No se pudo completar la operación")
            status = response.status_code
            response.headers["X-Trace-ID"] = trace_id
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            method = request.method if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else "OTHER"
            duration = perf_counter() - started
            if route != "/metrics":
                telemetry.requests.labels(method, route, str(status)).inc()
                telemetry.duration.labels(method, route).observe(duration)
                telemetry.log("http_request", method=method, route=route, status=status, duration_ms=round(duration * 1000, 2))
            trace_id_context.reset(token)

    def error_response(request, status, code, message):
        return JSONResponse(status_code=status,
                            content={"error": {"code": code, "message": message}, "trace_id": request.state.trace_id},
                            headers={"X-Trace-ID": request.state.trace_id})

    @application.exception_handler(ServiceError)
    async def business_error(request, error):
        if request.method == "POST" and request.url.path == "/transactions":
            telemetry.transactions.labels("rejected").inc()
        telemetry.log("operation_rejected", code=error.code)
        return error_response(request, error.status, error.code, error.message)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        telemetry.log("validation_failed", error_count=len(error.errors()))
        return error_response(request, 422, "invalid_request", "Revisa los campos y las cabeceras de la petición")

    async def database_error(request, error):
        kind = error_kind(error)
        telemetry.db_errors.labels(kind).inc()
        if request.method == "POST" and request.url.path == "/transactions":
            telemetry.transactions.labels("failed").inc()
        telemetry.log("database_failure", level=logging.ERROR, kind=kind, sqlstate=getattr(error, "sqlstate", None))
        temporary = isinstance(error, (psycopg.OperationalError, PoolTimeout, TooManyRequests))
        return error_response(request, 503 if temporary else 500,
                              "database_busy" if temporary else "internal_error",
                              "Base de datos no disponible temporalmente" if temporary else "No se pudo completar la operación")

    application.add_exception_handler(psycopg.Error, database_error)
    application.add_exception_handler(PoolTimeout, database_error)
    application.add_exception_handler(TooManyRequests, database_error)

    @application.post("/transactions", response_model=TransferResponse, status_code=201,
                      responses={200: {"model": TransferResponse}, 409: {"description": "Conflicto o saldo insuficiente"}})
    def post_transaction(body: TransferRequest, request: Request, response: Response, idempotency_key: IdempotencyKey):
        result, created = create_transfer(request.app.state.database, body, idempotency_key, request.state.trace_id)
        response.status_code = 201 if created else 200
        outcome = "completed" if created else "duplicate"
        telemetry.transactions.labels(outcome).inc()
        telemetry.log("transaction_" + outcome, transaction_id=result["transaction_id"])
        return result

    @application.get("/transactions/{transaction_id}", response_model=TransferResponse)
    def read_transaction(transaction_id: UUID, request: Request):
        return get_transfer(request.app.state.database, transaction_id)

    @application.get("/transactions/{transaction_id}/recommendation", response_model=RecommendationResponse)
    def read_recommendation(transaction_id: UUID, request: Request):
        return get_recommendation(request.app.state.database, transaction_id)

    @application.get("/accounts/{account_id}", response_model=AccountResponse)
    def read_account(account_id: UUID, request: Request):
        return get_account(request.app.state.database, account_id)

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.get("/ready")
    def ready(request: Request):
        database = request.app.state.database
        with database.connection() as connection:
            database.query(connection, "readiness", "SELECT 1")
        return {"status": "ready"}

    @application.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(generate_latest(telemetry.registry), media_type=CONTENT_TYPE_LATEST)

    return application


app = create_app()
