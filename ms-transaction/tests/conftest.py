import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row


@pytest.fixture(scope="session")
def database_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not conninfo_to_dict(url).get("dbname", "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL debe apuntar a una base desechable terminada en _test")
    return url


@pytest.fixture
def db(database_url):
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as connection:
        connection.execute("TRUNCATE ai_jobs, transactions, accounts")
        seed = Path(__file__).resolve().parents[2] / "database" / "seed.sql"
        connection.execute(seed.read_text(encoding="utf-8"))
        yield connection


@pytest.fixture
def client(db, database_url):
    from app.config import Settings
    from app.main import create_app

    application = create_app(Settings(database_url=database_url))
    with TestClient(application, raise_server_exceptions=False) as test_client:
        yield test_client
