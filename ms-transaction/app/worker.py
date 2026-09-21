"""Procesador de ai_jobs. Ejecutar como proceso separado: python -m app.worker."""

import logging
import signal
import threading
from time import perf_counter

import httpx
import psycopg
from prometheus_client import start_http_server
from psycopg.types.json import Jsonb
from psycopg_pool import PoolTimeout, TooManyRequests

from app.config import Settings
from app.database import Database
from app.schemas import AIResult
from app.telemetry import Telemetry, error_kind, trace_id_context


class Worker:
    def __init__(self, settings):
        self.settings = settings
        self.telemetry = Telemetry("transaction-worker")
        self.db = Database(settings, self.telemetry)
        self.http = httpx.Client(timeout=settings.ai_timeout, trust_env=False)

    def open(self):
        self.db.open()

    def close(self):
        self.http.close()
        self.db.close()

    def recover_expired(self):
        with self.db.connection() as connection:
            rows = self.db.query(connection, "recover_jobs", """
                UPDATE ai_jobs
                SET status = CASE WHEN attempts < 3 THEN 'pending'::ai_job_status
                                  ELSE 'failed'::ai_job_status END,
                    locked_until = NULL, next_attempt_at = now(),
                    last_error_code = 'lease_expired'
                WHERE status = 'processing' AND locked_until <= now()
                RETURNING transaction_id, attempts, status
            """).fetchall()
        for row in rows:
            self.telemetry.log("job_reservation_expired", level=logging.WARNING, **row)

    def claim_job(self):
        # La reserva se confirma antes de llamar a IA: no quedan locks durante HTTP.
        with self.db.connection() as connection, connection.transaction():
            job = self.db.query(connection, "select_job", """
                SELECT j.transaction_id, t.amount, t.currency, t.trace_id
                FROM ai_jobs j JOIN transactions t USING (transaction_id)
                WHERE j.status = 'pending' AND j.next_attempt_at <= now()
                    AND t.status = 'completed'
                ORDER BY j.next_attempt_at, j.transaction_id
                LIMIT 1 FOR UPDATE OF j SKIP LOCKED
            """).fetchone()
            if job is None:
                return None
            reservation = self.db.query(connection, "reserve_job", """
                UPDATE ai_jobs SET status = 'processing', attempts = attempts + 1,
                    locked_until = now() + %s * interval '1 second'
                WHERE transaction_id = %s
                RETURNING attempts, locked_until
            """, (self.settings.job_lease_seconds, job["transaction_id"])).fetchone()
            return {**job, **reservation}

    def finish_job(self, job, *, result=None, error_code=None, retryable=False):
        if result is not None:
            status = "completed"
        else:
            status = "pending" if retryable and job["attempts"] < 3 else "failed"
        with self.db.connection() as connection:
            updated = self.db.query(connection, "finish_job", """
                UPDATE ai_jobs SET status = %s::ai_job_status, locked_until = NULL,
                    result = %s, last_error_code = %s,
                    completed_at = CASE WHEN %s = 'completed' THEN now() ELSE NULL END,
                    next_attempt_at = now() + %s * interval '1 second'
                WHERE transaction_id = %s AND status = 'processing'
                    AND attempts = %s AND locked_until = %s AND locked_until > now()
                RETURNING transaction_id
            """, (status, Jsonb(result) if result is not None else None, error_code,
                  status, 2 ** job["attempts"], job["transaction_id"],
                  job["attempts"], job["locked_until"])).fetchone()
        self.telemetry.log("job_finished" if updated else "job_reservation_lost",
                           transaction_id=job["transaction_id"], attempt=job["attempts"],
                           status=status, error_code=error_code)
        return updated is not None

    def call_ai(self, job):
        started = perf_counter()
        result, error_code, retryable = None, None, False
        try:
            response = self.http.post(
                self.settings.ai_service_url,
                json={"transaction_id": str(job["transaction_id"]),
                      "amount": job["amount"], "currency": job["currency"]},
                headers={"X-Trace-ID": job["trace_id"]},
            )
            if not 200 <= response.status_code < 300:
                error_code = f"ai_http_{response.status_code}"
                retryable = response.status_code == 429 or response.status_code >= 500
            else:
                parsed = AIResult.model_validate(response.json())
                if parsed.transaction_id != job["transaction_id"]:
                    raise ValueError("transaction_id no coincide")
                result = parsed.model_dump(mode="json")
        except httpx.TimeoutException:
            error_code, retryable = "ai_timeout", True
        except httpx.RequestError:
            error_code, retryable = "ai_unavailable", True
        except ValueError:
            error_code = "invalid_ai_response"
        finally:
            elapsed = perf_counter() - started
            self.telemetry.ai_duration.observe(elapsed)
        self.telemetry.ai_requests.labels("success" if result else "failure").inc()
        self.telemetry.log("ai_call_finished", transaction_id=job["transaction_id"],
                           attempt=job["attempts"], error_code=error_code,
                           duration_ms=round(elapsed * 1000, 2))
        return result, error_code, retryable

    def refresh_metrics(self):
        with self.db.connection() as connection:
            rows = self.db.query(connection, "job_metrics", """
                SELECT status, count(*) AS total,
                    extract(epoch FROM now() - min(created_at)) AS oldest_age
                FROM ai_jobs GROUP BY status
            """).fetchall()
        by_status = {row["status"]: row for row in rows}
        for status in ("pending", "processing", "completed", "failed"):
            self.telemetry.jobs.labels(status).set(by_status.get(status, {}).get("total", 0))
        self.telemetry.oldest_job.set(max(0, float(by_status.get("pending", {}).get("oldest_age", 0))))

    def process_once(self):
        self.recover_expired()
        job = self.claim_job()
        if job is None:
            return False
        token = trace_id_context.set(job["trace_id"])
        try:
            self.telemetry.log("job_started", transaction_id=job["transaction_id"], attempt=job["attempts"])
            result, error_code, retryable = self.call_ai(job)
            self.finish_job(job, result=result, error_code=error_code, retryable=retryable)
        finally:
            trace_id_context.reset(token)
        return True


def main():
    settings = Settings.from_env()
    worker = Worker(settings)
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    server = None
    try:
        worker.open()
        server, _ = start_http_server(settings.metrics_port, addr="0.0.0.0", registry=worker.telemetry.registry)
        worker.telemetry.log("worker_started")
        next_metrics_at = 0
        while not stop.is_set():
            try:
                # Los totales históricos no deben recalcularse por transferencia.
                if perf_counter() >= next_metrics_at:
                    next_metrics_at = perf_counter() + 15
                    worker.refresh_metrics()
                if worker.process_once():
                    continue
            except (psycopg.Error, PoolTimeout, TooManyRequests) as error:
                worker.telemetry.db_errors.labels(error_kind(error)).inc()
                worker.telemetry.log("worker_database_error", level=logging.ERROR,
                                     sqlstate=getattr(error, "sqlstate", None))
            stop.wait(settings.worker_poll_seconds)
    finally:
        if server:
            server.shutdown()
            server.server_close()
        worker.close()


if __name__ == "__main__":
    main()
