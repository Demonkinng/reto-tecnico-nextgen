import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest

from test_transactions import transfer


@pytest.fixture
def ai_server():
    state = {"status": 200, "invalid": False, "received": [], "started": threading.Event(), "release": threading.Event()}
    state["release"].set()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["received"].append((body, dict(self.headers)))
            state["started"].set()
            state["release"].wait(timeout=3)
            result = {"transaction_id": body["transaction_id"], "recommendation": "Reserva parte de tu presupuesto para imprevistos.", "mode": "mock"}
            if state["invalid"]:
                result["transaction_id"] = str(uuid4())
            payload = json.dumps(result).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}/recommendations"
    yield state
    state["release"].set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


@pytest.fixture
def worker(database_url, ai_server):
    from app.config import Settings
    from app.worker import Worker

    instance = Worker(Settings(database_url=database_url, ai_service_url=ai_server["url"], ai_timeout=.5, job_lease_seconds=2))
    instance.open()
    yield instance
    instance.close()


def test_worker_persists_valid_response_and_trace(client, worker, ai_server, db):
    transaction_id = transfer(client).json()["transaction_id"]
    assert worker.process_once() is True
    job = db.execute("SELECT * FROM ai_jobs").fetchone()
    assert job["status"] == "completed" and job["attempts"] == 1
    assert job["locked_until"] is None
    assert job["result"]["transaction_id"] == transaction_id
    payload, headers = ai_server["received"][0]
    assert set(payload) == {"transaction_id", "amount", "currency"}
    assert headers["X-Trace-ID"] == "trace-test-1"
    assert client.get(f"/transactions/{transaction_id}/recommendation").json()["result"]["mode"] == "mock"
    assert worker.process_once() is False


def test_temporary_ai_errors_stop_after_three_attempts(client, worker, ai_server, db):
    transfer(client)
    ai_server["status"] = 503
    for attempt in range(1, 4):
        assert worker.process_once()
        job = db.execute("SELECT * FROM ai_jobs").fetchone()
        assert job["attempts"] == attempt
        assert job["status"] == ("pending" if attempt < 3 else "failed")
        if attempt < 3:
            assert worker.process_once() is False
            db.execute("UPDATE ai_jobs SET next_attempt_at = now()")
    assert worker.process_once() is False
    assert db.execute("SELECT balance FROM accounts ORDER BY account_id").fetchone()["balance"] == 97500


@pytest.mark.parametrize("status,invalid,code", [(400, False, "ai_http_400"), (200, True, "invalid_ai_response")])
def test_permanent_or_mismatched_ai_response_is_not_retried(client, worker, ai_server, db, status, invalid, code):
    transfer(client)
    ai_server["status"] = status
    ai_server["invalid"] = invalid
    worker.process_once()
    job = db.execute("SELECT * FROM ai_jobs").fetchone()
    assert job["status"] == "failed" and job["attempts"] == 1
    assert job["last_error_code"] == code
    assert job["result"] is None


def test_expired_reservation_is_recovered_and_old_attempt_cannot_write(client, worker, db):
    transfer(client)
    old_job = worker.claim_job()
    assert old_job["attempts"] == 1
    db.execute("UPDATE ai_jobs SET locked_until = now() - interval '1 second'")
    worker.recover_expired()
    new_job = worker.claim_job()
    assert new_job["attempts"] == 2
    result = {"transaction_id": str(old_job["transaction_id"]), "recommendation": "late", "mode": "mock"}
    assert worker.finish_job(old_job, result=result) is False
    assert db.execute("SELECT status FROM ai_jobs").fetchone()["status"] == "processing"


def test_exhausted_expired_reservation_is_marked_failed(client, worker, db):
    transfer(client)
    db.execute("UPDATE ai_jobs SET status='processing', attempts=3, locked_until=now()-interval '1 second'")
    assert worker.process_once() is False
    row = db.execute("SELECT * FROM ai_jobs").fetchone()
    assert row["status"] == "failed" and row["last_error_code"] == "lease_expired"


def test_two_workers_cannot_claim_the_same_job(client, worker, db):
    transfer(client)
    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(lambda _: worker.claim_job(), range(2)))
    assert sum(job is not None for job in claimed) == 1
    assert db.execute("SELECT attempts FROM ai_jobs").fetchone()["attempts"] == 1


def test_slow_ai_does_not_hold_account_locks_or_block_transfers(client, worker, ai_server, db):
    transfer(client)
    ai_server["release"].clear()
    with ThreadPoolExecutor(max_workers=1) as executor:
        processing = executor.submit(worker.process_once)
        assert ai_server["started"].wait(timeout=2)
        response = transfer(client, key="while-ai-waits")
        assert response.status_code == 201
        assert ai_server["release"].is_set() is False
        ai_server["release"].set()
        assert processing.result(timeout=3)
    assert db.execute("SELECT count(*) AS n FROM transactions").fetchone()["n"] == 2


def test_ai_timeout_is_retryable_and_visible_in_metrics(client, worker, ai_server, db):
    from prometheus_client import generate_latest

    transfer(client)
    ai_server["release"].clear()
    assert worker.process_once()
    job = db.execute("SELECT * FROM ai_jobs").fetchone()
    assert job["status"] == "pending" and job["attempts"] == 1
    assert job["last_error_code"] == "ai_timeout"
    worker.refresh_metrics()
    metrics = generate_latest(worker.telemetry.registry).decode()
    assert 'ai_requests_total{result="failure"} 1.0' in metrics
    assert 'ai_jobs{state="pending"} 1.0' in metrics
