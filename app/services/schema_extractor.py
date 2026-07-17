"""
Schema extractor.
Connects to Postgres via psycopg3 (read-only, no writes ever issued) and
pulls everything the rest of the pipeline needs:
  - tables + columns (information_schema)
  - primary keys / foreign keys (information_schema constraints)
  - table + column comments (pg_catalog obj_description / col_description)
  - row count estimates (pg_class.reltuples, cheap and good enough)
  - sampled distinct values for low-cardinality text columns ("value linking" --
    lets the LLM see that JobTitle actually contains 'Design Engineer', not
    just that a column called JobTitle exists)
"""
from __future__ import annotations
import logging
from typing import Iterable
import psycopg
from app.config.settings import settings
from app.models.schema_models import ColumnInfo, ForeignKeyInfo, TableInfo
logger = logging.getLogger(__name__)

class SchemaExtractor:
    def __init__(self, dsn: str | None = None, schemas: list[str] | None = None):
        self.dsn = dsn or settings.dsn
        self.schemas = schemas or settings.schema_include

    # Public entry point
    def extract(self) -> dict[str, TableInfo]:
        """Run the full extraction and return {qualified_table_name: TableInfo}."""
        with psycopg.connect(self.dsn) as conn:
            # Belt-and-braces: never allow this session to write anything,
            # even if a bug somewhere tried to.
            conn.execute("SET default_transaction_read_only = on;")
            tables = self._fetch_tables(conn)
            self._attach_columns(conn, tables)
            self._attach_primary_keys(conn, tables)
            self._attach_foreign_keys(conn, tables)
            self._attach_comments(conn, tables)
            self._attach_row_estimates(conn, tables)
            self._sample_values(conn, tables)

        logger.info("Extracted %d tables across schemas %s", len(tables), self.schemas)
        return tables

    # Individual extraction steps
    def _fetch_tables(self, conn) -> dict[str, TableInfo]:
        rows = conn.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND table_schema = ANY(%s)
            ORDER BY table_schema, table_name;
            """,
            (self.schemas,),
        ).fetchall()
        tables = {}
        for schema, name in rows:
            t = TableInfo(schema=schema, name=name)
            tables[t.qualified_name] = t
        return tables
    
    def _attach_columns(self, conn, tables: dict[str, TableInfo]) -> None:
        rows = conn.execute(
            """
            SELECT table_schema, table_name, column_name, data_type,
                   is_nullable, ordinal_position
            FROM information_schema.columns
            WHERE table_schema = ANY(%s)
            ORDER BY table_schema, table_name, ordinal_position;
            """,
            (self.schemas,),
        ).fetchall()
        for schema, table, col, dtype, nullable, pos in rows:
            key = f"{schema}.{table}"
            if key not in tables:
                continue
            tables[key].columns[col] = ColumnInfo(
                name=col,
                data_type=dtype,
                is_nullable=(nullable == "YES"),
                ordinal_position=pos,
            )

    def _attach_primary_keys(self, conn, tables: dict[str, TableInfo]) -> None:
        rows = conn.execute(
            """
            SELECT tc.table_schema, tc.table_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = ANY(%s);
            """,
            (self.schemas,),
        ).fetchall()
        for schema, table, col in rows:
            key = f"{schema}.{table}"
            if key not in tables:
                continue
            tables[key].primary_keys.append(col)
            if col in tables[key].columns:
                tables[key].columns[col].is_primary_key = True

    def _attach_foreign_keys(self, conn, tables: dict[str, TableInfo]) -> None:
        rows = conn.execute(
            """
            SELECT
                tc.table_schema  AS fk_schema,
                tc.table_name    AS fk_table,
                kcu.column_name  AS fk_column,
                ccu.table_schema AS ref_schema,
                ccu.table_name   AS ref_table,
                ccu.column_name  AS ref_column,
                tc.constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = ANY(%s);
            """,
            (self.schemas,),
        ).fetchall()
        for fk_schema, fk_table, fk_col, ref_schema, ref_table, ref_col, cname in rows:
            key = f"{fk_schema}.{fk_table}"
            if key not in tables:
                continue
            tables[key].foreign_keys.append(
                ForeignKeyInfo(
                    column=fk_col,
                    ref_schema=ref_schema,
                    ref_table=ref_table,
                    ref_column=ref_col,
                    constraint_name=cname,
                )
            )
            if fk_col in tables[key].columns:
                tables[key].columns[fk_col].is_foreign_key = True

    def _attach_comments(self, conn, tables: dict[str, TableInfo]) -> None:
        table_rows = conn.execute(
            """
            SELECT n.nspname, c.relname, obj_description(c.oid)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r' AND n.nspname = ANY(%s);
            """,
            (self.schemas,),
        ).fetchall()
        for schema, table, comment in table_rows:
            key = f"{schema}.{table}"
            if key in tables and comment:
                tables[key].comment = comment

        col_rows = conn.execute(
            """
            SELECT n.nspname, c.relname, a.attname, col_description(c.oid, a.attnum)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid
            WHERE c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
              AND n.nspname = ANY(%s);
            """,
            (self.schemas,),
        ).fetchall()
        for schema, table, col, comment in col_rows:
            key = f"{schema}.{table}"
            if key in tables and col in tables[key].columns and comment:
                tables[key].columns[col].comment = comment

    def _attach_row_estimates(self, conn, tables: dict[str, TableInfo]) -> None:
        rows = conn.execute(
            """
            SELECT n.nspname, c.relname, c.reltuples::bigint
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r' AND n.nspname = ANY(%s);
            """,
            (self.schemas,),
        ).fetchall()
        for schema, table, estimate in rows:
            key = f"{schema}.{table}"
            if key in tables:
                tables[key].row_estimate = int(estimate) if estimate is not None else None

    def _sample_values(self, conn, tables: dict[str, TableInfo]) -> None:
        """
        Value linking: for eligible low-cardinality text/boolean columns,
        pull the actual distinct values so retrieval can later match a
        user's phrase (e.g. "engineers") against real stored strings
        (e.g. 'Design Engineer'). Uses pg_stats.n_distinct as a cheap pre-filter before ever running
        a DISTINCT query, and skips PK/FK id columns entirely -- sampling
        those is wasted work and pollutes the prompt with meaningless data.
        """
        stats_rows = conn.execute(
            """
            SELECT schemaname, tablename, attname, n_distinct
            FROM pg_stats
            WHERE schemaname = ANY(%s);
            """,
            (self.schemas,),
        ).fetchall()
        stats = {(s, t, c): nd for s, t, c, nd in stats_rows}

        for key, table in tables.items():
            for col_name, col in table.columns.items():
                if col.is_primary_key or col.is_foreign_key:
                    continue
                if col.data_type not in settings.sample_eligible_types:
                    continue

                n_distinct = stats.get((table.schema, table.name, col_name))
                if not self._is_low_cardinality(n_distinct, table.row_estimate):
                    continue

                try:
                    values = conn.execute(
                        f'SELECT DISTINCT "{col_name}" FROM "{table.schema}"."{table.name}" '
                        f'WHERE "{col_name}" IS NOT NULL LIMIT %s;',
                        (settings.sample_values_limit,),
                    ).fetchall()
                    col.sample_values = [str(v[0]) for v in values]
                except Exception as exc:  
                    logger.warning("Value sampling failed for %s.%s: %s", key, col_name, exc)

    @staticmethod
    def _is_low_cardinality(n_distinct: float | None, row_estimate: int | None) -> bool:
        if n_distinct is None:
            return False
        if n_distinct >= 0:
            # Positive value = absolute estimate of distinct values.
            return n_distinct <= settings.sample_distinct_threshold
        # Negative value = -(distinct/rowcount) ratio.
        if row_estimate and row_estimate > 0:
            estimated_distinct = abs(n_distinct) * row_estimate
            return estimated_distinct <= settings.sample_distinct_threshold
        return False