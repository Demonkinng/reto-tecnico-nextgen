import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

A = "00000000-0000-4000-8000-000000000001"
B = "00000000-0000-4000-8000-000000000002"
C = "00000000-0000-4000-8000-000000000003"


def transfer(client, key="demo-1", **changes):
    body = {"source_account_id": A, "destination_account_id": B, "amount": 2500, "currency": "USD"}
    body.update(changes)
    return client.post("/transactions", headers={"Idempotency-Key": key, "X-Trace-ID": "trace-test-1"}, json=body)


def test_transfer_changes_both_balances_and_creates_one_job(client, db):
    response = transfer(client)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "completed"
    assert result["amount"] == 2500
    assert result["completed_at"] is not None
    assert result["trace_id"] == "trace-test-1"
    assert response.headers["X-Trace-ID"] == "trace-test-1"
    balances = db.execute("SELECT balance FROM accounts ORDER BY account_id").fetchall()
    assert [row["balance"] for row in balances] == [97500, 52500, 0]
    job = db.execute("SELECT * FROM ai_jobs").fetchone()
    assert str(job["transaction_id"]) == result["transaction_id"]
    assert job["status"] == "pending" and job["attempts"] == 0
    assert client.get(f"/transactions/{result['transaction_id']}").json() == result
    assert client.get(f"/transactions/{result['transaction_id']}/recommendation").json()["status"] == "pending"
    assert client.get(f"/accounts/{A}").json()["balance"] == 97500


@pytest.mark.parametrize("amount", [0, -1, 1.5, "100", True, 9223372036854775808])
def test_invalid_amount_does_not_write(client, db, amount):
    assert transfer(client, amount=amount).status_code == 422
    assert db.execute("SELECT count(*) AS n FROM transactions").fetchone()["n"] == 0


@pytest.mark.parametrize("changes,status", [
    ({"destination_account_id": A}, 422),
    ({"currency": "EUR"}, 422),
    ({"destination_account_id": str(uuid4())}, 404),
    ({"source_account_id": C}, 409),
])
def test_rejected_transfer_leaves_no_partial_writes(client, db, changes, status):
    assert transfer(client, **changes).status_code == status
    assert db.execute("SELECT sum(balance) AS total FROM accounts").fetchone()["total"] == 150000
    assert db.execute("SELECT count(*) AS n FROM transactions").fetchone()["n"] == 0
    assert db.execute("SELECT count(*) AS n FROM ai_jobs").fetchone()["n"] == 0


def test_repeat_returns_original_even_when_balance_is_now_insufficient(client, db):
    original = transfer(client, amount=90000)
    repeated = transfer(client, amount=90000)
    assert original.status_code == 201 and repeated.status_code == 200
    assert original.json() == repeated.json()
    assert transfer(client, amount=90001).status_code == 409
    assert db.execute("SELECT balance FROM accounts WHERE account_id = %s", (A,)).fetchone()["balance"] == 10000


def test_concurrent_same_key_moves_money_once(client, db):
    with ThreadPoolExecutor(max_workers=6) as executor:
        responses = list(executor.map(lambda _: transfer(client, amount=90000), range(6)))
    assert sorted(response.status_code for response in responses) == [200] * 5 + [201]
    assert len({response.json()["transaction_id"] for response in responses}) == 1
    assert db.execute("SELECT balance FROM accounts WHERE account_id = %s", (A,)).fetchone()["balance"] == 10000
    assert db.execute("SELECT count(*) AS n FROM ai_jobs").fetchone()["n"] == 1


def test_concurrent_spending_never_overdraws(client, db):
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(lambda n: transfer(client, key=f"concurrent-{n}", amount=40000), range(4)))
    assert sorted(response.status_code for response in responses) == [201, 201, 409, 409]
    balances = db.execute("SELECT balance FROM accounts ORDER BY account_id").fetchall()
    assert [row["balance"] for row in balances] == [20000, 130000, 0]


def test_opposite_transfers_use_consistent_lock_order(client, db):
    def send(n):
        return transfer(client, key=f"opposite-{n}", amount=500,
                        source_account_id=A if n % 2 == 0 else B,
                        destination_account_id=B if n % 2 == 0 else A)
    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(send, range(8)))
    assert [response.status_code for response in responses] == [201] * 8
    assert [r["balance"] for r in db.execute("SELECT balance FROM accounts ORDER BY account_id")] == [100000, 50000, 0]


def test_destination_overflow_is_rejected(client, db):
    db.execute("UPDATE accounts SET balance = 9223372036854775807 WHERE account_id = %s", (B,))
    assert transfer(client, amount=1).status_code == 409
    assert db.execute("SELECT balance FROM accounts WHERE account_id = %s", (A,)).fetchone()["balance"] == 100000


def test_accounts_with_unsupported_currency_cannot_be_used(client, db):
    # The user's current schema accepts other currencies: enforce the API's USD contract.
    db.execute("UPDATE accounts SET currency = 'EUR' WHERE account_id = %s", (B,))
    assert transfer(client).status_code == 409
    assert db.execute("SELECT count(*) AS n FROM transactions").fetchone()["n"] == 0


def test_job_insert_failure_rolls_back_money_and_transfer(client, db):
    db.execute("""CREATE FUNCTION reject_test_job() RETURNS trigger LANGUAGE plpgsql AS $$
                  BEGIN RAISE EXCEPTION 'test-only job failure'; END $$""")
    db.execute("CREATE TRIGGER reject_test_job BEFORE INSERT ON ai_jobs FOR EACH ROW EXECUTE FUNCTION reject_test_job()")
    try:
        response = transfer(client)
        assert response.status_code == 500
        assert "test-only" not in response.text
        assert [r["balance"] for r in db.execute("SELECT balance FROM accounts ORDER BY account_id")] == [100000, 50000, 0]
        assert db.execute("SELECT count(*) AS n FROM transactions").fetchone()["n"] == 0
    finally:
        db.execute("DROP TRIGGER reject_test_job ON ai_jobs")
        db.execute("DROP FUNCTION reject_test_job()")


def test_lock_timeout_returns_traceable_503(client, db, caplog):
    with db.transaction():
        db.execute("SELECT account_id FROM accounts WHERE account_id = %s FOR UPDATE", (A,))
        response = transfer(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_busy"
    assert response.headers["X-Trace-ID"] == "trace-test-1"
    assert db.execute("SELECT count(*) AS n FROM transactions").fetchone()["n"] == 0
    events = [json.loads(record.message) for record in caplog.records if record.name == "smartbancs"]
    failure = next(event for event in events if event["event"] == "db_operation_failed")
    assert failure["operation"] == "lock_accounts"
    assert failure["sqlstate"] == "55P03"
    assert failure["backend_pid"] > 0
    assert failure["trace_id"] == "trace-test-1"


def test_missing_resources_headers_and_metrics(client):
    assert client.get(f"/transactions/{uuid4()}").status_code == 404
    assert client.get("/transactions/not-a-uuid").status_code == 422
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.post("/transactions", json={}).status_code == 422
    transfer(client)
    metrics = client.get("/metrics").text
    assert 'transactions_total{result="completed"} 1.0' in metrics
    assert 'route="/transactions/{transaction_id}"' in metrics
    assert "trace-test-1" not in metrics
    assert A not in metrics


def test_unexpected_error_is_sanitized_and_has_trace(client, monkeypatch):
    import app.main

    def broken_operation(*args):
        raise RuntimeError("sensitive internal detail")

    monkeypatch.setattr(app.main, "create_transfer", broken_operation)
    response = transfer(client)
    assert response.status_code == 500
    assert response.headers["X-Trace-ID"] == "trace-test-1"
    assert response.json()["error"]["code"] == "internal_error"
    assert "sensitive" not in response.text
