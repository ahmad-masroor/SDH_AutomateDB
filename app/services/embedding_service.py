""" Embedding service for schema retrieval. Turns each table into a short text "document" (name, comment, columns,
types, and sampled example values) and embeds it with a local sentence-transformers model. 
Model: BAAI/bge-small-en-v1.5 """
from __future__ import annotations
import logging
import os 
# transformers (a dependency of sentence-transformers) auto-detects
# TensorFlow if it's installed on the system and tries to load its TF
# integration path, which breaks on Keras 3 with a ValueError. We only
# need the PyTorch backend, so tell transformers to skip TF entirely -
# this must happen BEFORE sentence_transformers/transformers are imported.
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_TORCH", "1") 
import numpy as np
from sentence_transformers import SentenceTransformer 
from app.models.schema_models import TableInfo
logger = logging.getLogger(__name__) 
MODEL_NAME = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: " 
 
class SchemaEmbeddingService:
    def __init__(self, model_name: str = MODEL_NAME):
        logger.info("Loading embedding model '%s' (first run downloads it once)...", model_name)
        self.model = SentenceTransformer(model_name)
 
    @staticmethod
    def build_table_document(table: TableInfo) -> str:
        parts = [table.name, table.name]
        if table.comment:
            parts.append(table.comment)
 
        parts.append("Columns: " + ", ".join(table.columns.keys()))
 
        for col in table.columns.values():
            if col.comment:
                parts.append(col.comment)
            if col.sample_values:
                parts.append(", ".join(col.sample_values[:5]))
 
        return ". ".join(parts)
 
    def embed_tables(self, tables: dict[str, TableInfo]) -> tuple[list[str], np.ndarray]:
        """Returns (table names in row order, float32 embedding matrix)."""
        names = list(tables.keys())
        docs = [self.build_table_document(tables[n]) for n in names]
        embeddings = self.model.encode(
            docs,
            normalize_embeddings=True,  # so inner product == cosine similarity
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return names, embeddings.astype("float32")
 
    def embed_query(self, question: str) -> np.ndarray:
        vec = self.model.encode(
            QUERY_PREFIX + question,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return vec.astype("float32")