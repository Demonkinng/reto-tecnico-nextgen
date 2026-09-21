"""Protecciones del verificador que consulta PostgreSQL."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("pipeline_verifier", Path(__file__).resolve().parents[2] / "scripts" / "verify_pipeline.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


@pytest.mark.parametrize("url,dsn", [
    ("http://127.0.0.1:8000", "host=127.0.0.1 port=55432 dbname=smartbancs_test"),
    ("http://remote.example:58000", "host=127.0.0.1 port=55432 dbname=smartbancs_test"),
    ("http://127.0.0.1:58000", "host=remote.example port=55432 dbname=smartbancs_test"),
    ("http://127.0.0.1:58000", "host=127.0.0.1 port=55432 dbname=another_test"),
    ("http://127.0.0.1:58000", "host=127.0.0.1 port=5432 dbname=smartbancs_test"),
    ("http://127.0.0.1:58000", "host=localhost hostaddr=203.0.113.1 port=55432 dbname=smartbancs_test"),
])
def test_verifier_rejects_targets_outside_explicit_test_environment(url, dsn):
    with pytest.raises(ValueError):
        verifier.validate_targets(url, dsn, api_port=58000, db_port=55432)


def test_verifier_accepts_explicit_loopback_test_ports():
    verifier.validate_targets("http://127.0.0.1:58000", "host=127.0.0.1 port=55432 dbname=smartbancs_test",
                              api_port=58000, db_port=55432)
