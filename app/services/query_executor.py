""" Query executor.
Runs an already-validated statement using the read-only role from
settings.reader_dsn — never the admin credentials used for introspection.
This is the second, database-enforced layer underneath the validator: even
if a statement somehow got past sql_validator.py, this role has no grant
to write anything, so Postgres itself refuses it (see
scripts/setup_reader_role.py, which also verifies this at setup time).
"""
from __future__ import annotations
import logging
import psycopg
from psycopg.rows import dict_row
logger = logging.getLogger(__name__)

class ExecutionError(Exception):
    """Raised when the database rejects a statement. Carries the real
    Postgres error message so the caller can feed it back to the model
    for a retry."""
    def __init__(self, message: str, pg_error: str):
        super().__init__(message)
        self.pg_error = pg_error

class QueryExecutor:
    def __init__(self, reader_dsn: str, statement_timeout_seconds: int = 10):
        self.reader_dsn = reader_dsn
        self.statement_timeout_seconds = statement_timeout_seconds

    def execute(self, sql: str) -> list[dict]:
        try:
            with psycopg.connect(self.reader_dsn, autocommit=True, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(f"SET statement_timeout = {self.statement_timeout_seconds * 1000};")
                    cur.execute(sql)
                    return cur.fetchall()
        except psycopg.Error as exc:
            pg_message = str(exc).strip()
            logger.info("Query execution failed (this may trigger a retry): %s", pg_message)
            raise ExecutionError(
                f"the database rejected the generated query: {pg_message}", pg_error=pg_message
            ) from exc