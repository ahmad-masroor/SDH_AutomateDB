""" Build the FAISS retrieval index over the schema metadata
cached by scripts/build_schema_cache.py. """
from __future__ import annotations
import logging
import pickle
import sys
from pathlib import Path
#Root level log config
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config.settings import settings
from app.models.schema_models import TableInfo
from app.services.cache_manager import CacheManager
from app.services.embedding_service import SchemaEmbeddingService
from app.services.retriever import SchemaRetriever

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def main() -> None:
    cache = CacheManager(settings.cache_dir / "app_cache.sqlite3")
    metadata = cache.get_json("schema_metadata")
    graph_bytes = cache.get_bytes("schema_graph")
    if metadata is None or graph_bytes is None:
        print("No cached schema metadata found.")
        print("Run `python scripts/build_schema_cache.py` first.")
        sys.exit(1)

    tables = {qname: TableInfo.from_dict(data) for qname, data in metadata.items()}
    graph = pickle.loads(graph_bytes)
    print(f"Loaded {len(tables)} tables from cache.\n")
    print("Loading embedding model (first run downloads it once)...")
    embedding_service = SchemaEmbeddingService()
    print("Now Embedding all tables and building FAISS index...")
    retriever = SchemaRetriever.build(tables, graph, embedding_service)
    # FAISS index: an optimized structure for nearest-neighbor search over vectors, used to make schema retrieval fast and scalable instead of having to compare the question against every table's description every time. 
    retriever.save(settings.faiss_dir)
    print(f"FAISS index saved to {settings.faiss_dir}\n")

    print("--- Sample retrievals ---")
    sample_questions = [
        "List employees hired after 2020",
        "Show me all vendors and their purchase orders",
        "What products are in the Bikes category",
        "Which sales territories have the highest revenue",
    ]
    for q in sample_questions:
        raw_scores = retriever.retrieve_debug(q, top_k=6)
        result = retriever.retrieve(q)
        print(f"\nQ: {q}")
        print("  Raw embedding scores (top 6):")
        for name, score in raw_scores:
            print(f"    {score:.3f}  {name}")
        print(f"  Final retrieved tables: {result}")

if __name__ == "__main__":
    main()