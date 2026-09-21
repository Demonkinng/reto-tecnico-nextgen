"""Verifica el flujo en compose.test.yml; escribe sólo en el entorno desechable."""

import os
import time
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict


def validate_targets(url, dsn, *, api_port, db_port):
    target = urlparse(url)
    database = conninfo_to_dict(dsn)
    if (api_port in {8000, 8001, 8002} or not 1024 <= api_port <= 65535
            or db_port == 5432 or not 1024 <= db_port <= 65535):
        raise ValueError("Los puertos deben identificar el entorno desechable, no desarrollo")
    if (target.scheme != "http" or target.hostname not in {"localhost", "127.0.0.1"}
            or target.port != api_port or target.path not in {"", "/"}
            or target.username or target.password or target.query or target.fragment):
        raise ValueError("Usa la API local desechable de compose.test.yml")
    if (database.get("host") not in {"localhost", "127.0.0.1"}
            or database.get("hostaddr", "127.0.0.1") != "127.0.0.1"
            or database.get("dbname") != "smartbancs_test" or database.get("port") != str(db_port)):
        raise ValueError("Usa smartbancs_test en loopback y el puerto de pruebas configurado")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    url = os.environ["TEST_API_URL"]
    dsn = os.environ["TEST_DATABASE_URL"]
    validate_targets(url, dsn, api_port=int(os.getenv("TEST_API_PORT", "58000")),
                     db_port=int(os.getenv("TEST_DB_PORT", "55432")))
    source = "00000000-0000-4000-8000-000000000001"
    destination = "00000000-0000-4000-8000-000000000002"
    trace_id = "pipeline-" + str(uuid4())
    with psycopg.connect(dsn, hostaddr="127.0.0.1", autocommit=True) as db, httpx.Client(base_url=url, timeout=5, trust_env=False) as client:
        before = db.execute("SELECT balance FROM accounts WHERE account_id = %s", (source,)).fetchone()[0]
        body = {"source_account_id": source, "destination_account_id": destination, "amount": 1, "currency": "USD"}
        headers = {"Idempotency-Key": trace_id, "X-Trace-ID": trace_id}
        first = client.post("/transactions", json=body, headers=headers)
        require(first.status_code == 201, f"Se esperaba 201 al crear; se recibió {first.status_code}")
        transaction_id = first.json()["transaction_id"]
        repeated = client.post("/transactions", json=body, headers=headers)
        require(repeated.status_code == 200 and repeated.json() == first.json(), "Falló la idempotencia")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            response = client.get(f"/transactions/{transaction_id}/recommendation")
            response.raise_for_status()
            job = response.json()
            if job["status"] in {"completed", "failed"}:
                break
            time.sleep(.1)
        require(job["status"] == "completed", "El trabajo no terminó correctamente")
        require(job["result"]["mode"] == "mock", "El resultado no proviene del mock")
        require(job["result"]["transaction_id"] == transaction_id, "El resultado pertenece a otra transferencia")
        stored = db.execute("SELECT status, result, locked_until FROM ai_jobs WHERE transaction_id = %s", (transaction_id,)).fetchone()
        require(stored is not None and stored[0] == "completed" and stored[1] == job["result"] and stored[2] is None,
                "El resultado no está correctamente persistido")
        after = db.execute("SELECT balance FROM accounts WHERE account_id = %s", (source,)).fetchone()[0]
        require(after == before - 1, "El saldo no refleja un solo débito")
        print(f"OK: transferencia, repeticion sin doble debito, worker y resultado mock guardado. trace_id={trace_id}")


if __name__ == "__main__":
    main()
