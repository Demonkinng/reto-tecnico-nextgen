import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from psycopg_pool import PoolTimeout, TooManyRequests

trace_id_context = ContextVar("trace_id", default=None)
logger = logging.getLogger("smartbancs")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


def error_kind(error):
    if isinstance(error, (PoolTimeout, TooManyRequests)):
        return "pool_exhausted"
    return {
        "55P03": "lock_timeout",
        "57014": "statement_timeout",
        "40P01": "deadlock",
        "40001": "serialization_failure",
    }.get(getattr(error, "sqlstate", None), "database_error")


class Telemetry:
    def __init__(self, service="ms-transaction"):
        self.service = service
        self.registry = CollectorRegistry()
        self.requests = Counter("http_requests_total", "Peticiones HTTP", ["method", "route", "status"], registry=self.registry)
        self.duration = Histogram("http_request_duration_seconds", "Duración HTTP", ["method", "route"], buckets=(.01, .05, .1, .25, .5, 1, 2, 5, 10), registry=self.registry)
        self.transactions = Counter("transactions_total", "Resultado de transferencias", ["result"], registry=self.registry)
        self.db_duration = Histogram("db_operation_duration_seconds", "Duración de operaciones SQL", ["operation"], registry=self.registry)
        self.db_errors = Counter("db_errors_total", "Fallos en operaciones de base", ["kind"], registry=self.registry)
        self.pool_wait = Histogram("db_pool_wait_seconds", "Espera para obtener conexión", registry=self.registry)
        self.ai_requests = Counter("ai_requests_total", "Llamadas de IA", ["result"], registry=self.registry)
        self.ai_duration = Histogram("ai_request_duration_seconds", "Duración de llamadas de IA", registry=self.registry)
        self.jobs = Gauge("ai_jobs", "Tareas de IA por estado", ["state"], registry=self.registry)
        self.oldest_job = Gauge("ai_oldest_pending_age_seconds", "Edad de la tarea pendiente más antigua", registry=self.registry)

    def log(self, event, level=logging.INFO, **fields):
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": logging.getLevelName(level), "service": self.service,
            "event": event, "trace_id": trace_id_context.get(), **fields,
        }
        logger.log(level, json.dumps(payload, default=str, ensure_ascii=False))
