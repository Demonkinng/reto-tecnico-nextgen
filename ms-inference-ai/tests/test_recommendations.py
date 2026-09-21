import asyncio
import json
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

TRANSACTION_ID = "0e3d735e-9856-40a5-8ba6-cb9769e4dbe8"


def payload(amount=2500, **changes):
    return {"transaction_id": TRANSACTION_ID, "amount": amount, "currency": "USD", **changes}


@pytest.fixture
def client():
    from app.main import create_app
    from app.config import Settings

    with TestClient(create_app(Settings()), raise_server_exceptions=False) as instance:
        yield instance


def test_contract_and_repeated_requests_are_deterministic(client, caplog):
    headers = {"X-Trace-ID": "ai-demo-1"}
    first = client.post("/recommendations", json=payload(), headers=headers)
    second = client.post("/recommendations", json=payload(), headers=headers)
    assert first.status_code == 200
    assert first.json() == second.json()
    assert set(first.json()) == {"transaction_id", "recommendation", "mode"}
    assert first.json()["transaction_id"] == TRANSACTION_ID
    assert first.json()["mode"] == "mock"
    assert 1 <= len(first.json()["recommendation"]) <= 2000
    assert first.headers["X-Trace-ID"] == "ai-demo-1"
    events = [json.loads(record.message) for record in caplog.records if record.name == "smartbancs.ai"]
    generated = next(event for event in events if event["event"] == "recommendation_generated")
    assert generated["trace_id"] == "ai-demo-1"
    assert generated["transaction_id"] == TRANSACTION_ID
    assert generated["rule_version"] == "mock-v1"
    assert all("recommendation" not in event and "amount" not in event for event in events)


def test_demo_ranges_change_at_documented_boundaries(client):
    responses = {amount: client.post("/recommendations", json=payload(amount)).json()["recommendation"]
                 for amount in (1, 9999, 10000, 49999, 50000, 9223372036854775807)}
    assert responses[1] == responses[9999]
    assert responses[10000] == responses[49999]
    assert responses[50000] == responses[9223372036854775807]
    assert len(set(responses.values())) == 3


@pytest.mark.parametrize("changes", [
    {"amount": 0}, {"amount": -1}, {"amount": 1.5}, {"amount": "100"},
    {"amount": True}, {"amount": 9223372036854775808},
    {"currency": "EUR"}, {"transaction_id": "invalid"},
    {"owner_name": "private-name"},
])
def test_invalid_input_is_rejected_without_echoing_data(client, changes):
    response = client.post("/recommendations", json=payload(**changes))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "private-name" not in response.text


@pytest.mark.parametrize("trace", [None, "x" * 65, "invalid trace"])
def test_missing_or_invalid_trace_is_replaced(client, trace):
    headers = {} if trace is None else {"X-Trace-ID": trace}
    response = client.post("/recommendations", json=payload(), headers=headers)
    assert response.status_code == 200
    UUID(response.headers["X-Trace-ID"])


def test_health_and_metrics_have_bounded_labels(client):
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    client.post("/recommendations", json=payload(), headers={"X-Trace-ID": "metric-trace"})
    for n in range(3):
        client.get(f"/not-found-{n}")
    metrics = client.get("/metrics").text
    assert 'http_requests_total{method="POST",route="/recommendations",status="200"} 1.0' in metrics
    assert 'inference_requests_total{result="success"} 1.0' in metrics
    assert 'route="unmatched"' in metrics
    assert TRANSACTION_ID not in metrics and "metric-trace" not in metrics
    assert "/not-found-" not in metrics


def test_simulated_unavailability_is_visible_and_does_not_kill_process():
    from app.config import Settings
    from app.main import create_app

    with TestClient(create_app(Settings(failure_mode="unavailable"))) as client:
        response = client.post("/recommendations", json=payload(), headers={"X-Trace-ID": "failed-ai"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "mock_unavailable"
        assert response.headers["X-Trace-ID"] == "failed-ai"
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        assert 'inference_requests_total{result="simulated_failure"} 1.0' in client.get("/metrics").text


def test_slow_recommendation_does_not_block_health():
    from app.config import Settings
    from app.main import create_app

    async def exercise():
        application = create_app(Settings(delay_seconds=.3))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
            pending = asyncio.create_task(client.post("/recommendations", json=payload()))
            await asyncio.sleep(.05)
            assert not pending.done()
            assert (await client.get("/health")).status_code == 200
            assert not pending.done()
            assert (await pending).status_code == 200

    asyncio.run(exercise())


@pytest.mark.parametrize("settings", [{"delay_seconds": -1}, {"delay_seconds": 11},
    {"delay_seconds": float("nan")}, {"delay_seconds": float("inf")}, {"failure_mode": "random"}])
def test_invalid_simulation_settings_fail_early(settings):
    from app.config import Settings

    with pytest.raises(ValueError):
        Settings(**settings)


def test_unexpected_error_does_not_leak_details(client, monkeypatch):
    import app.main

    def broken(*args):
        raise RuntimeError("private-provider-detail")

    monkeypatch.setattr(app.main, "recommend", broken)
    response = client.post("/recommendations", json=payload(), headers={"X-Trace-ID": "unexpected-ai"})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.headers["X-Trace-ID"] == "unexpected-ai"
    assert "private-provider-detail" not in response.text
