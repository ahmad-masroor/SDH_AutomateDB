# Database Automation

A local first natural language to SQL pipeline. A person asks a plain English question, the system finds the relevant tables in a Postgres database, writes a SQL statement, checks it, runs it, and returns real rows.

## Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Database | PostgreSQL, psycopg3 | Source of truth, read only introspection and execution |
| Schema cache | SQLite | Stores extracted schema metadata and the graph, so runtime never reads the live database |
| Graph | NetworkX | Table and column relationship graph built from foreign keys |
| Embeddings | sentence transformers, BAAI/bge small en v1.5 | Turns each table into a vector for similarity search |
| Vector search | FAISS (IndexFlatIP) | Millisecond similarity search over table embeddings |
| SQL generation | Ollama (Qwen2.5 Coder) or Groq (Llama 3.3 70B) | Writes the candidate SQL statement from the assembled prompt |
| SQL safety | sqlglot | Parses and validates the generated statement before execution |
| Backend API | FastAPI | Exposes the pipeline over HTTP |
| Frontend | React, Vite | Question box and results view |

## Project Layout

| Part | Contains |
|---|---|
| app/config | Central settings, DSNs, cache and index paths, sampling thresholds |
| app/models | Shared data classes: TableInfo, ColumnInfo, ForeignKeyInfo |
| app/services | The pipeline itself, setup phase and runtime phase |
| app/api | FastAPI endpoint that builds the pipeline once at startup |
| frontend | React and Vite single page app calling that endpoint |
| scripts | Command line entry points for setup and manual testing |

## Two Phase Design

The system runs in two distinct phases.

| Phase | Runs | Reads the live database | Produces |
|---|---|---|---|
| Setup | Once, or whenever the schema changes | Yes | Schema cache and FAISS index |
| Runtime | On every question asked | No, cache only | SQL result for that question |

## Setup Phase

Run in this order: `scripts/build_schema_cache.py`, then `scripts/build_retrieval_index.py`, then `scripts/setup_reader_role.py` (one time only, or after a password change).

| File | Role |
|---|---|
| schema_extractor.py | Connects to Postgres as an admin, introspection only user. Reads information_schema and pg_catalog for every table, column, type, primary key, foreign key, and comment. Samples real distinct values for low cardinality text columns so plain English phrasing can later be matched to actual stored values |
| graph_builder.py | Builds a NetworkX directed graph. Table and column nodes, connected by has_column, belongs_to, references, and fk_table edges. Lets the system pull in tables connected to a question's topic that were never named directly |
| cache_manager.py | A small SQLite key value store. Persists the extracted schema metadata and the pickled graph to metadata_cache/app_cache.sqlite3 |
| embedding_service.py | Converts each table into a text document from its name, comment, columns, column comments, and sample values, then embeds it with the local BGE model |
| retriever.py (index building half) | Builds a FAISS inner product index from those embeddings and saves it to faiss_index |
| setup_reader_role.py | Creates a dedicated read only Postgres role and verifies it genuinely cannot run a write statement |

## Runtime Phase

Every question, whether typed into `scripts/ask_sql.py` or submitted through the React frontend to the FastAPI backend, moves through these files in this exact order.

| Order | File | Role |
|---|---|---|
| 1 | retriever.py | Embeds the question, searches the FAISS index, expands across the schema graph for foreign key connected tables, then applies a value match boost and a table name match boost. Returns a short table shortlist |
| 2 | prompt_builder.py | Assembles one prompt from that shortlist: columns, types, comments, sample values, real join relationships, and the question. Adds the previous failed SQL and database error on a retry |
| 3 | sql_generator.py | Sends the prompt to a SQL writing LLM, Ollama or Groq, at temperature zero, and returns one candidate statement |
| 4 | sql_validator.py | Parses the candidate with sqlglot, blocks anything that is not a plain SELECT, and enforces a row limit |
| 5 | query_executor.py | Executes the validated statement through the dedicated read only role and surfaces real Postgres errors upward |
| 6 | sql_pipeline.py | The orchestrator. Owns the self correction loop: on a failed execution, the real error is fed back into prompt_builder for another attempt, up to three tries, before returning an honest failure |
| 7 | app/api/main.py, frontend | Builds this same pipeline once at startup and exposes it over HTTP for the React page |

## Constraints

| Constraint | Enforced in | Why |
|---|---|---|
| Only SELECT statements reach the database | sql_validator.py | Every other statement type, including ones nested inside a read looking query, is blocked before execution |
| A row limit is always present | sql_validator.py | Adds LIMIT if missing, caps it if the model asked for too many rows |
| Execution runs under a read only role, never admin credentials | query_executor.py, setup_reader_role.py | Two independent layers: the validator blocks writes in code, the database itself refuses them regardless |
| Runtime never touches the live schema | cache_manager.py, retriever.py | Only the cached metadata and graph are read after setup |
| Temperature fixed at zero | sql_generator.py | Same question should produce the same query every time |
| Statement timeout of ten seconds | query_executor.py | Prevents a runaway query from hanging the pipeline |
| Up to three self correction attempts | sql_pipeline.py | Balances giving the model a real chance to fix a mistake against failing honestly instead of guessing forever |
| Only low cardinality text columns are sampled | schema_extractor.py | Keeps sample values meaningful and avoids wasted extraction work on high cardinality columns |
| Bare foreign key ids are avoided in results | prompt_builder.py | The person asking rarely knows the schema well enough to request a join explicitly, so ids are resolved to a readable name column |
| The production version must run entirely on local infrastructure | sql_generator.py (Ollama path) | Groq is present only for faster development iteration and is expected to be removed or made optional before the client facing release |

