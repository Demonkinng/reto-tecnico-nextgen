import os
from dataclasses import dataclass, field

from psycopg.conninfo import make_conninfo


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    pool_size: int = 10
    pool_timeout: float = 0.5
    lock_timeout_ms: int = 500
    statement_timeout_ms: int = 1000
    ai_service_url: str = "http://ms-inference-ai:8000/recommendations"
    ai_timeout: float = 5.0
    job_lease_seconds: int = 30
    worker_poll_seconds: float = 1.0
    metrics_port: int = 8001

    @classmethod
    def from_env(cls):
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            password = os.getenv("DB_PASSWORD")
            if not password:
                raise ValueError("Configura DB_PASSWORD o DATABASE_URL")
            database_url = make_conninfo(
                host=os.getenv("DB_HOST", "localhost"),
                port=os.getenv("DB_PORT", "5432"),
                dbname=os.getenv("DB_NAME", "smartbancs"),
                user=os.getenv("DB_USER", "postgres"),
                password=password,
            )
        return cls(
            database_url=database_url,
            ai_service_url=os.getenv("AI_SERVICE_URL", cls.ai_service_url),
            pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
            metrics_port=int(os.getenv("METRICS_PORT", "8001")),
        )
