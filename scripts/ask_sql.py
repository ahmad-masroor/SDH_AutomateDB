""" This takes a question all the way through generation, validation, execution, and the self-correction retry
loop, and shows you the actual rows. 
Requires Ollama running locally with the model in .env already pulled: ollama pull qwen2.5-coder:7b
For now, using Groq API LLM instead of Ollama because of memory constraints."""
from __future__ import annotations
import logging
import pickle
import sys
from pathlib import Path
 
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config.settings import settings
from app.models.schema_models import TableInfo
from app.services.cache_manager import CacheManager
from app.services.embedding_service import SchemaEmbeddingService
from app.services.prompt_builder import PromptBuilder
from app.services.query_executor import QueryExecutor
from app.services.retriever import SchemaRetriever
from app.services.sql_generator import GroqSQLGenerator, OllamaSQLGenerator
from app.services.sql_pipeline import SQLPipeline
from app.services.sql_validator import SQLValidator
from datetime import datetime
 
settings.ensure_dirs()
log_file = settings.logs_dir / f"ask_sql_{datetime.now():%Y%m%d}.log"
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),  # still prints to the console as before
        logging.FileHandler(log_file, encoding="utf-8"),  # and now persists here
    ],
)
logging.getLogger(__name__).info("Logging to %s", log_file.resolve())
 
 
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
def main() -> None:
    cache = CacheManager(settings.cache_dir / "app_cache.sqlite3")
    metadata = cache.get_json("schema_metadata")
    graph_bytes = cache.get_bytes("schema_graph")
    if metadata is None or graph_bytes is None:
        print("No cached schema metadata found. Run build_schema_cache.py first.")
        sys.exit(1)
 
    tables = {qname: TableInfo.from_dict(data) for qname, data in metadata.items()}
    graph = pickle.loads(graph_bytes)
    if not (settings.faiss_dir / "schema.index").exists():
        print("No FAISS index found. Run build_retrieval_index.py first.")
        sys.exit(1)
    if not settings.reader_db_password:
        print("APP_READER_DB_PASSWORD is not set in .env. Run scripts/setup_reader_role.py first.")
        sys.exit(1)
    print("Loading embedding model...")
    embedding_service = SchemaEmbeddingService()
    retriever = SchemaRetriever.load(settings.faiss_dir, tables, graph, embedding_service)
    if settings.llm_provider == "groq":
        print(f"Connecting to Groq Cloud Service, model '{settings.groq_model}'...")
        llm = GroqSQLGenerator(
            model=settings.groq_model,
            api_key=settings.groq_api_key,
            temperature=settings.ollama_temperature,
        )
    else:
        print(f"Connecting to Ollama at {settings.ollama_host}, model '{settings.ollama_model}'...")
        llm = OllamaSQLGenerator(
            model=settings.ollama_model,
            host=settings.ollama_host,
            temperature=settings.ollama_temperature,
            timeout_seconds=settings.ollama_timeout_seconds,
            num_ctx=settings.ollama_num_ctx,
        ) 
    validator = SQLValidator(dialect="postgres", max_rows=settings.sql_max_rows)
    executor = QueryExecutor(settings.reader_dsn, settings.sql_statement_timeout_seconds)
    prompt_builder = PromptBuilder(dialect="PostgreSQL", max_rows=settings.sql_max_rows)
    pipeline = SQLPipeline(
        tables=tables, graph=graph, retriever=retriever, llm=llm,
        validator=validator, executor=executor, prompt_builder=prompt_builder,
        max_retries=settings.sql_max_retries,
    ) 
    print("\nType a question, blank line to quit.\n")
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
 
        result = pipeline.answer(question)
        _print_result(result)
 
def _print_result(result: dict) -> None:
    status = result["status"]
    if status == "success":
        print(f"\nSQL ({result['attempts']} attempt(s)):\n  {result['sql']}\n")
        rows = result["rows"]
        print(f"{len(rows)} row(s):")
        for row in rows[:20]:
            print(" ", row)
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more")
    elif status == "needs_clarification":
        print(f"\n{result['message']}")
    elif status == "blocked":
        print(f"\nBlocked: {result['message']}")
    else:
        print(f"\nFailed: {result['message']}")
    print()
 
if __name__ == "__main__":
    main()