""" Central configuration.
All values can be overridden via a `.env` file in the project root prefixed with APP_.
Nothing here reaches the network except the local Postgres connection.
"""
from __future__ import annotations
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
 
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="APP_",
        extra="ignore",
    )
    # Database connection
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "adventureworks"
    db_user: str = "postgres"
    db_password: str = ""
    # Create this once with scripts/setup_reader_role.py (run as the admin
    # user above). The pipeline never executes a generated statement under
    # the admin credentials — only this role, which physically cannot
    # write anything, no matter what the validator or the model do.
    reader_db_user: str = "nlsql_reader"
    reader_db_password: str = ""
    schema_include: list[str] = Field(
        default_factory=lambda: [
            "humanresources",
            "person",
            "production",
            "purchasing",
            "sales",
        ]
    ) 
    # --- Value sampling (for value-linking, see architecture notes) ---
    # Only sample distinct values for columns whose estimated distinct count
    # is at or below this. Keeps us from ever doing `SELECT DISTINCT` on a
    # high-cardinality column (e.g. free text, IDs) or a huge table.
    sample_distinct_threshold: int = 50
    sample_values_limit: int = 25
    # Only these Postgres data types are candidates for value sampling.
    sample_eligible_types: list[str] = Field(
        default_factory=lambda: [
            "character varying",
            "character",
            "text",
            "boolean",
        ]
    )
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    ollama_temperature: float = 0.0
    ollama_timeout_seconds: int = 120
    ollama_num_ctx: int = 4096 #cap for smaller context windows due to memory limitations, 4096 for qwen2.5-coder:7b
    llm_provider: str = "ollama"
 
    # --- Groq (non local)
    groq_api_key: str = ""
    groq_model: str = "qwen-2.5-coder-32b"
 
    # --- Phase 3: SQL validation + execution ---
    sql_max_rows: int = 500
    sql_statement_timeout_seconds: int = 10
    sql_max_retries: int = 3
 
    # --- Local paths ---
    cache_dir: Path = Path("metadata_cache")
    faiss_dir: Path = Path("faiss_index")
    logs_dir: Path = Path("logs")
 
    @property
    def dsn(self) -> str:
        """Admin Postgres connection string for psycopg3. Introspection only."""
        return (
            f"host={self.db_host} port={self.db_port} "
            f"dbname={self.db_name} user={self.db_user} "
            f"password={self.db_password}"
        )
    @property
    def reader_dsn(self) -> str:
        """Read-only connection string. This is what generated SQL actually runs under."""
        return (
            f"host={self.db_host} port={self.db_port} "
            f"dbname={self.db_name} user={self.reader_db_user} "
            f"password={self.reader_db_password}"
        ) 
    def ensure_dirs(self) -> None:
        for d in (self.cache_dir, self.faiss_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)
 
settings = Settings()