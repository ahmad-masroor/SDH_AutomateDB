"""Schema retriever - the hybrid retrieval strategy from the architecture doc:
    question
      -> embedding search (top ~20 candidate tables)
      -> knowledge-graph 1-hop expansion (pull in FK-connected tables)
      -> value-match boost (question text contains a real sampled value,
         e.g. "Design Engineer" -> force humanresources.employee in)
      -> table-name match boost (question directly names a table's own
         concept, e.g. "vendor" -> force purchasing.vendor in, even when
         embedding rank alone doesn't surface it reliably for short,
         generic table names)
      -> final capped list (~10 tables) handed to the prompt builder
"""
from __future__ import annotations
import logging
import re
from pathlib import Path
from typing import Protocol
import faiss
import numpy as np
from app.models.schema_models import TableInfo
from app.services.graph_builder import SchemaGraphBuilder
logger = logging.getLogger(__name__)
class EmbeddingProvider(Protocol):
    def embed_tables(self, tables: dict[str, TableInfo]) -> tuple[list[str], np.ndarray]: ...
    def embed_query(self, question: str) -> np.ndarray: ...

class SchemaRetriever:
    def __init__(
        self,
        tables: dict[str, TableInfo],
        graph,
        embedding_provider: EmbeddingProvider,
        index: faiss.Index,
        table_names: list[str],
    ):
        self.tables = tables
        self.graph = graph
        self.embedding_provider = embedding_provider
        self.index = index
        self.table_names = table_names  # row order matches the FAISS index

    # Construction / persistence
    @classmethod
    def build(cls, tables: dict[str, TableInfo], graph, embedding_provider: EmbeddingProvider) -> "SchemaRetriever":
        names, embeddings = embedding_provider.embed_tables(tables)
        dim = embeddings.shape[1]
        index = faiss.IndexFlatIP(dim)  # normalized vectors -> inner product == cosine similarity
        index.add(embeddings)
        return cls(tables, graph, embedding_provider, index, names)

    def save(self, faiss_dir: Path) -> None:
        faiss_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(faiss_dir / "schema.index"))
        (faiss_dir / "table_names.txt").write_text("\n".join(self.table_names), encoding="utf-8")

    @classmethod
    def load(
        cls, faiss_dir: Path, tables: dict[str, TableInfo], graph, embedding_provider: EmbeddingProvider
    ) -> "SchemaRetriever":
        index = faiss.read_index(str(faiss_dir / "schema.index"))
        names = (faiss_dir / "table_names.txt").read_text(encoding="utf-8").splitlines()
        return cls(tables, graph, embedding_provider, index, names)

    # Retrieval
    def retrieve(self, question: str, top_k: int = 6, max_final: int = 12) -> list[str]:
        """
        top_k is intentionally small (6, not 20) relative to max_final*2 (24):
        it needs to leave real room for expand_related_tables to pull in
        FK-connected tables. If top_k >= max_final*2, expansion never runs
        at all (see graph_builder.expand_related_tables docstring) and this
        silently degrades into plain embedding top-K with no graph signal.
        max_final has been raised twice now (8 -> 10 -> 12), each time
        after a real failure case, and both times the actual bottleneck
        was the same: expand_related_tables' own internal search (run with
        a budget of max_final*2) genuinely found the right table, but
        retrieve()'s own final truncation back down to max_final threw it
        away anyway, because too many other seeds' expansions were
        already occupying those slots. That pattern, not any single
        anecdote, is why this is now set structurally at top_k * 2: with
        top_k seeds each potentially deserving at least one full hop of
        expansion, top_k + top_k is the actual minimum for every seed to
        get a fair turn, not a number picked to fit one failing question.
        Confirmed against two independent real cases (a vendor join
        needing 10, a sales-order join needing 12) that this is enough
        headroom for both without needing a third bump.
        """
        seed_tables = [name for name, _score in self._embedding_search(question, top_k)]
        expanded = SchemaGraphBuilder.expand_related_tables(
            self.graph, seed_tables, max_tables=max_final * 2
        )
        value_hits = self._value_match_boost(question)
        name_hits = self._table_name_match_boost(question)

        # Table-name hits go first (the question named this exact concept
        # directly, about the strongest signal retrieval can get), then
        # value hits (near-certain matches on real data), then the
        # embedding+graph results, deduplicated, capped at max_final.
        final = list(dict.fromkeys(name_hits + value_hits + expanded))[:max_final]
        return final

    def retrieve_debug(self, question: str, top_k: int = 10) -> list[tuple[str, float]]:
        """Raw embedding similarity scores, unfiltered by graph expansion
        or value matching - use this to diagnose why a table is or isn't
        being retrieved for a given question."""
        return self._embedding_search(question, top_k)

    def _embedding_search(self, question: str, top_k: int) -> list[tuple[str, float]]:
        query_vec = self.embedding_provider.embed_query(question).reshape(1, -1)
        k = min(top_k, len(self.table_names))
        scores, idxs = self.index.search(query_vec, k)
        return [
            (self.table_names[i], float(scores[0][j]))
            for j, i in enumerate(idxs[0])
            if i != -1 #FAISS returns -1 as a placeholder index when there are fewer real matches than requested, so this filters those placeholders out rather than crashing on table_names[-1]
        ]

    def _value_match_boost(self, question: str) -> list[str]:
        """If the question contains a real stored value verbatim (case
        insensitive), surface that table regardless of embedding rank.
        This is the payoff of the value-sampling step in the extractor."""
        q_lower = question.lower()
        hits: list[str] = []
        for qname, table in self.tables.items():
            for col in table.columns.values():
                if any(val and val.lower() in q_lower for val in col.sample_values):
                    hits.append(qname)
                    break
        return hits

    # Generic connectors that should never be treated as a meaningful concept to match against table names, no matter how short the schema's own table names happen to be.
    _STOPWORDS = frozenset({
        "what", "which", "does", "show", "list", "have", "with", "from",
        "this", "that", "each", "their", "there", "been", "were", "when",
        "where", "much", "many", "total", "give", "find", "along",
    })

    def _table_name_match_boost(self, question: str) -> list[str]:
        """
        Two directions, both handled here:

        1. The question directly names a table's own concept as one word
           (e.g. "vendor" for purchasing.vendor). Whole-word match on the
           table's bare name, since that's the one case a person can be
           expected to say verbatim, no one types a concatenated name
           like "purchaseorderheader".

        2. A word FROM the question appears inside a compound table name
           (e.g. "category" inside "productcategory"). This direction
           matters just as much: a person writes "product category" as
           two separate words, which will never equal the single token
           "productcategory" under direction 1 alone. Confirmed missing
           in practice - "what percentage does each product category
           represent" never surfaced production.productcategory at all,
           purely because the match only ever ran in one direction.

           Direction 2 is filtered by specificity: a word is only
           trusted if it appears in a small number of table names (<=3).
           "category" appears in 2 table names (productcategory,
           productsubcategory) - specific enough to trust completely.
           "product" appears in 16 - far too generic to force-include on
           its own, it would flood the result with barely-related
           tables, so words that unspecific are skipped for this check
           and left to embedding search and graph expansion instead.
        """
        q_lower = question.lower()
        hits: list[str] = []

        # Direction 1: whole table name appears in the question.
        for qname, table in self.tables.items():
            bare_name = table.name.lower()
            if len(bare_name) < 3:
                continue
            pattern = r"\b" + re.escape(bare_name) + r"s?\b"
            if re.search(pattern, q_lower):
                hits.append(qname)

        # Direction 2: a question word appears inside a table name.
        words = {w for w in re.findall(r"[a-z]+", q_lower) if len(w) >= 4 and w not in self._STOPWORDS}
        for word in words:
            matches = [qname for qname, table in self.tables.items() if word in table.name.lower()]
            if 0 < len(matches) <= 3:
                hits.extend(m for m in matches if m not in hits)

        return hits