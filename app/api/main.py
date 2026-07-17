""" FastAPI Backend"""
from __future__ import annotations
import logging
import pickle
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
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

settings.ensure_dirs()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(settings.logs_dir / "api.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Natural language to SQL API")

# The React dev server runs on a different port (Vite default: 5173),
# which makes every request cross-origin during development. Restricted
# to localhost dev ports only 
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
pipeline: SQLPipeline | None = None  # built once in the startup event below
class AskRequest(BaseModel):
    question: str

class AskResponse(BaseModel):
    status: str
    sql: str | None = None
    rows: list[dict] | None = None
    attempts: int | None = None
    tables_used: list[str] | None = None
    message: str | None = None

@app.on_event("startup")
def build_pipeline() -> None:
    global pipeline

    cache = CacheManager(settings.cache_dir / "app_cache.sqlite3")
    metadata = cache.get_json("schema_metadata")
    graph_bytes = cache.get_bytes("schema_graph")
    if metadata is None or graph_bytes is None:
        raise RuntimeError(
            "No cached schema metadata found. Run scripts/build_schema_cache.py "
            "before starting the API."
        )
    tables = {qname: TableInfo.from_dict(data) for qname, data in metadata.items()}
    graph = pickle.loads(graph_bytes)
    if not (settings.faiss_dir / "schema.index").exists():
        raise RuntimeError(
            "No FAISS index found. Run scripts/build_retrieval_index.py before "
            "starting the API."
        )
    if not settings.reader_db_password:
        raise RuntimeError(
            "APP_READER_DB_PASSWORD is not set in .env. Run "
            "scripts/setup_reader_role.py before starting the API."
        )
    logger.info("Loading embedding model...")
    embedding_service = SchemaEmbeddingService()
    retriever = SchemaRetriever.load(settings.faiss_dir, tables, graph, embedding_service)
    if settings.llm_provider == "groq":
        logger.info("Connecting to Groq (cloud), model '%s'...", settings.groq_model)
        llm = GroqSQLGenerator(
            model=settings.groq_model,
            api_key=settings.groq_api_key,
            temperature=settings.ollama_temperature,
        )
    else:
        logger.info("Connecting to Ollama at %s, model '%s'...", settings.ollama_host, settings.ollama_model)
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
    logger.info("Pipeline ready.")

@app.post("/api/ask", response_model=AskResponse)
def ask(request: AskRequest) -> AskResponse:
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline is not ready yet.")
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    logger.info("Question: %s", request.question)
    result = pipeline.answer(request.question)

    # SQLPipeline.answer() already returns a plain dict; this just makes
    # the shape explicit and typed for anything calling the API besides
    # the bundled UI.
    return AskResponse(
        status=result["status"],
        sql=result.get("sql"),
        rows=result.get("rows"),
        attempts=result.get("attempts"),
        tables_used=result.get("tables_used"),
        message=result.get("message"),
    )

@app.get("/api/health")
def health() -> dict:
    return {"status": "ready" if pipeline is not None else "not_ready"}