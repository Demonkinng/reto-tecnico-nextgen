import json
import logging
import sys
from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, Counter, Histogram

logger = logging.getLogger("smartbancs.ai")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = True


class Telemetry:
    def __init__(self):
        self.registry = CollectorRegistry()
        self.requests = Counter("http_requests_total", "Peticiones HTTP",
                                ["method", "route", "status"], registry=self.registry)
        self.duration = Histogram("http_request_duration_seconds", "Duración HTTP",
                                  ["method", "route"], buckets=(.01, .05, .1, .25, .5, 1, 2, 5, 10),
                                  registry=self.registry)
        self.inferences = Counter("inference_requests_total", "Resultados de inferencia simulada",
                                  ["result"], registry=self.registry)

    def log(self, event, trace_id, level=logging.INFO, **fields):
        logger.log(level, json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": logging.getLevelName(level), "service": "ms-inference-ai",
            "event": event, "trace_id": trace_id, "mode": "mock", **fields,
        }, ensure_ascii=False, default=str))
