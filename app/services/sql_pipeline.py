""" SQL pipeline orchestrator. All the pieces of the architecture gets implemented here:
      -> question
      -> retriever (embedding search + graph expansion + value match)
      -> prompt builder (schema + joins + question)
      -> LLM (SQL generation) 
      -> validator (static checks)
      -> executor (read-only execution)
      -> result (rows or error) """
from __future__ import annotations
import logging 
from app.models.schema_models import TableInfo
from app.services.prompt_builder import PromptBuilder
from app.services.query_executor import ExecutionError, QueryExecutor
from app.services.sql_generator import LLMProvider
from app.services.sql_validator import SQLValidator, ValidationError
 
logger = logging.getLogger(__name__) 
class SQLPipeline:
    def __init__(
        self,
        tables: dict[str, TableInfo],
        graph,
        retriever,
        llm: LLMProvider,
        validator: SQLValidator,
        executor: QueryExecutor,
        prompt_builder: PromptBuilder | None = None,
        max_retries: int = 3,
    ):
        self.tables = tables
        self.graph = graph
        self.retriever = retriever
        self.llm = llm
        self.validator = validator
        self.executor = executor
        self.prompt_builder = prompt_builder or PromptBuilder(max_rows=validator.max_rows)
        self.max_retries = max_retries
 
    def answer(self, question: str) -> dict:
        table_names = self.retriever.retrieve(question)
        logger.info("Retrieved tables for %r: %s", question, table_names)
 
        if not table_names:
            return {
                "status": "needs_clarification",
                "message": (
                    "No tables in the schema looked relevant to this question. "
                    "Could you mention the specific data you're asking about?"
                ),
            }
 
        retry_sql = None
        retry_error = None
        last_error = None
 
        for attempt in range(1, self.max_retries + 1):
            prompt = self.prompt_builder.build(
                question, self.tables, table_names, self.graph,
                retry_sql=retry_sql, retry_error=retry_error,
            )
            raw_sql = self.llm.generate(prompt)
            logger.info("Attempt %d raw SQL: %s", attempt, raw_sql)
 
            try:
                safe_sql = self.validator.validate_and_fix(raw_sql)
            except ValidationError as exc:
                return {
                    "status": "blocked",
                    "message": f"The generated statement was blocked before execution: {exc}",
                    "attempt": attempt,
                    "tables_used": table_names,
                }
 
            try:
                rows = self.executor.execute(safe_sql)
                return {
                    "status": "success",
                    "sql": safe_sql,
                    "rows": rows,
                    "attempts": attempt,
                    "tables_used": table_names,
                }
            except ExecutionError as exc:
                last_error = exc.pg_error
                retry_sql = raw_sql
                retry_error = last_error
                logger.info("Attempt %d failed, retrying with error context: %s", attempt, last_error)
 
        return {
            "status": "failed",
            "message": (
                f"The query still failed after {self.max_retries} attempts. "
                f"Last database error: {last_error}"
            ),
            "tables_used": table_names,
        }