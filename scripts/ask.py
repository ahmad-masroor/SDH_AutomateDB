""" Retrieval testing shell. Type any natural-language question and see which tables the retriever
would hand to the (not-yet-built) SQL-generating LLM. """
from __future__ import annotations
import pickle
import sys
from pathlib import Path
from app.config.settings import settings
from app.models.schema_models import TableInfo
from app.services.cache_manager import CacheManager
from app.services.embedding_service import SchemaEmbeddingService
from app.services.retriever import SchemaRetriever

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

    print("Loading embedding model...")
    #SchemaEmbeddingService wraps BAAI/bge-small-en-v1.5 embedding model
    embedding_service = SchemaEmbeddingService()
    retriever = SchemaRetriever.load(settings.faiss_dir, tables, graph, embedding_service)
    print("\nType a question, blank line to quit.")
    while True:
        try:
            q = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            break
        raw_scores = retriever.retrieve_debug(q[len("debug:"):].strip(), top_k=10)
        print("Raw embedding scores:")
        for name, score in raw_scores:
              print(f"  {score:.3f}  {name}")
        print()
        continue

if __name__ == "__main__":
    main()