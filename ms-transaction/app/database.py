import logging
from contextlib import contextmanager
from time import perf_counter

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class Database:
    def __init__(self, settings, telemetry):
        self.telemetry = telemetry
        self.pool = ConnectionPool(
            settings.database_url,
            min_size=1, max_size=settings.pool_size, max_waiting=20,
            timeout=settings.pool_timeout, open=False,
            kwargs={
                "autocommit": True, "row_factory": dict_row,
                "connect_timeout": 3, "application_name": telemetry.service,
                "options": f"-c timezone=UTC -c lock_timeout={settings.lock_timeout_ms} -c statement_timeout={settings.statement_timeout_ms}",
            },
        )

    def open(self):
        self.pool.open(wait=True, timeout=10)

    def close(self):
        self.pool.close()

    @contextmanager
    def connection(self):
        started = perf_counter()
        acquired = False
        try:
            with self.pool.connection() as connection:
                acquired = True
                self.telemetry.pool_wait.observe(perf_counter() - started)
                yield connection
        finally:
            if not acquired:
                self.telemetry.pool_wait.observe(perf_counter() - started)

    def query(self, connection, operation, sql, params=None):
        started = perf_counter()
        try:
            return connection.execute(sql, params)
        except Exception as error:
            self.telemetry.log("db_operation_failed", level=logging.ERROR,
                               operation=operation, sqlstate=getattr(error, "sqlstate", None),
                               backend_pid=connection.info.backend_pid,
                               duration_ms=round((perf_counter() - started) * 1000, 2))
            raise
        finally:
            elapsed = perf_counter() - started
            self.telemetry.db_duration.labels(operation).observe(elapsed)
            if elapsed >= .2:
                self.telemetry.log("db_operation_slow", level=logging.WARNING,
                                   operation=operation, backend_pid=connection.info.backend_pid,
                                   duration_ms=round(elapsed * 1000, 2))
