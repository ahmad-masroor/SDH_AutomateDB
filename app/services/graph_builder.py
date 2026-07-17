""" Knowledge graph builder.
Turns the flat dict of TableInfo objects from the extractor into a
NetworkX directed graph so the retriever can later do:
  "embedding search found these 20 tables -> expand 1 hop via FK edges
   -> get the 8 tables that are actually connected"
Node ids:
    table nodes  -> "schema.table"
    column nodes -> "schema.table.column"
Edge types (stored as an edge attribute):
    has_column   table  -> column
    belongs_to   column -> table          (reverse, cheap to have both directions)
    references   column -> column         (FK column -> the column it points to)
    fk_table     table  -> table          (coarse table-to-table shortcut, used for the "expand 1 hop" retrieval step)"""
from __future__ import annotations
import pickle
from pathlib import Path
import networkx as nx
from app.models.schema_models import TableInfo 
class SchemaGraphBuilder:
    def build(self, tables: dict[str, TableInfo]) -> nx.DiGraph:
        g = nx.DiGraph() 
        for qname, table in tables.items():
            g.add_node(
                qname,
                node_type="table",
                schema=table.schema,
                name=table.name,
                comment=table.comment,
                row_estimate=table.row_estimate,
            )
            for col_name, col in table.columns.items():
                col_id = f"{qname}.{col_name}"
                g.add_node(
                    col_id,
                    node_type="column",
                    table=qname,
                    name=col_name,
                    data_type=col.data_type,
                    is_primary_key=col.is_primary_key,
                    is_foreign_key=col.is_foreign_key,
                    comment=col.comment,
                    sample_values=col.sample_values,
                )
                g.add_edge(qname, col_id, kind="has_column")
                g.add_edge(col_id, qname, kind="belongs_to")
        # Second pass for FK edges: needs all column nodes to exist first.
        for qname, table in tables.items():
            for fk in table.foreign_keys:
                src_col = f"{qname}.{fk.column}"
                dst_table = f"{fk.ref_schema}.{fk.ref_table}"
                dst_col = f"{dst_table}.{fk.ref_column}"
                if src_col not in g or dst_col not in g:
                    # Referenced table is outside the schemas we extracted --
                    # skip rather than create a dangling edge.
                    continue
                g.add_edge(src_col, dst_col, kind="references", constraint=fk.constraint_name)
                # Coarse table-level shortcut in both directions for
                # retrieval expansion (a query rarely cares which direction
                # the FK points, only that the tables are related).
                g.add_edge(qname, dst_table, kind="fk_table")
                g.add_edge(dst_table, qname, kind="fk_table")
 
        return g
    # Retrieval helper: 1-hop expansion used by the retriever later
    @staticmethod
    def expand_related_tables(g: nx.DiGraph, table_names: list[str], max_tables: int = 8) -> list[str]:
        """ Given a seed list of table node ids (from embedding search), expand by
        1 hop over fk_table edges and return a deduplicated, capped list, seeds
        prioritized first. Seeds are truncated to max_tables before expansion runs, otherwise
        dumping all seeds into `result` first could fill the cap before any
        FK-neighbor expansion ever executes. Expansion is round-robin across seeds (one neighbor per seed per
        round), not one seed exhausted at a time. Otherwise a seed with many FK
        relationships (e.g. a central "product" table) can crowd out every
        other seed's neighbors before the loop reaches them. 
        """
        seeds = list(dict.fromkeys(table_names))[:max_tables]
        result = list(seeds)
        if len(result) >= max_tables:
            return result[:max_tables]
 
        neighbor_iters = []
        for t in seeds:
            if t not in g:
                continue
            neighbors = [n for n in g.successors(t) if g.nodes[n].get("node_type") == "table"]
            neighbor_iters.append(iter(neighbors))
 
        active = neighbor_iters
        while active and len(result) < max_tables:
            still_active = []
            for it in active:
                try:
                    neighbor = next(it)
                except StopIteration:
                    continue
                still_active.append(it)
                if neighbor not in result:
                    result.append(neighbor)
                    if len(result) >= max_tables:
                        break
            active = still_active
 
        return result[:max_tables]
    
    @staticmethod
    def save(g: nx.DiGraph, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(g, f)
    @staticmethod
    def load(path: Path) -> nx.DiGraph:
        with open(path, "rb") as f:
            return pickle.load(f)