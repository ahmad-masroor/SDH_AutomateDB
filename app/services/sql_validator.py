""" SQL validator.
Every statement the model generates passes through here before it is ever
handed to the query executor. Static parsing catches what it can — but it
cannot know a column doesn't actually exist, or that a join produces
duplicate rows. That class of error is caught downstream, at execution
time, by the retry loop in sql_pipeline.py; this module's job is narrower
and non-negotiable: never let anything but a plain read through, and
never let an unbounded read through either.
"""
from __future__ import annotations
import logging
import sqlglot
from sqlglot import exp
""" SQLGlot is a Python library for parsing, transpiling, optimizing, and executing SQL across 30+ database dialects. It enables consistent SQL formatting and seamless translation between dialects"""
logger = logging.getLogger(__name__)

FORBIDDEN_STATEMENT_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.TruncateTable,
    exp.Grant,
)
class ValidationError(Exception):
    """Raised when a generated statement cannot be made safe to execute."""

class SQLValidator:
    def __init__(self, dialect: str = "postgres", max_rows: int = 500):
        self.dialect = dialect
        self.max_rows = max_rows

    def validate_and_fix(self, sql: str) -> str:
        """Returns a safe, executable statement, or raises ValidationError."""
        cleaned = sql.strip().rstrip(";")
        try:
            parsed = sqlglot.parse_one(cleaned, read=self.dialect)
        except Exception as exc:  
            raise ValidationError(f"the statement did not parse as valid SQL: {exc}") from exc

        if parsed is None:
            raise ValidationError("no statement could be parsed from the model's output")

        if not isinstance(parsed, exp.Select):
            raise ValidationError(
                "only read (SELECT) statements are permitted; the generated statement was "
                f"a {type(parsed).__name__}, which has been blocked before it reached the database"
            )
        for node in parsed.walk():
            if isinstance(node, FORBIDDEN_STATEMENT_TYPES):
                raise ValidationError(
                    f"the statement contains a forbidden operation ({type(node).__name__}) "
                    "nested inside an otherwise read-looking query"
                )

        parsed = self._enforce_row_limit(parsed)
        return parsed.sql(dialect=self.dialect)

    def _enforce_row_limit(self, parsed: exp.Select) -> exp.Select:
        existing_limit = parsed.args.get("limit")
        if existing_limit is None:
            return parsed.limit(self.max_rows)
        try:
            current_value = int(existing_limit.expression.this)
        except (AttributeError, ValueError, TypeError):
            logger.warning("Could not parse existing LIMIT value, overriding with max_rows")
            parsed.set("limit", exp.Limit(expression=exp.Literal.number(self.max_rows)))
            return parsed

        if current_value > self.max_rows:
            parsed.set("limit", exp.Limit(expression=exp.Literal.number(self.max_rows)))
        return parsed