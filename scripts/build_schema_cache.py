""" Extract schema from Postgres -> build knowledge graph -> cache both.
Run from the project root:  python scripts/build_schema_cache.py
Prints a summary of what it found. """
from __future__ import annotations
import logging
import sys
from pathlib import Path
import pickle
from app.config.settings import settings
from app.services.cache_manager import CacheManager
from app.services.graph_builder import SchemaGraphBuilder
from app.services.schema_extractor import SchemaExtractor

# Allow running as `python scripts/build_schema_cache.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

def main() -> None:
    settings.ensure_dirs()
    print(f"Connecting to {settings.db_name} at {settings.db_host}:{settings.db_port} "
          f"as '{settings.db_user}' ...")
    print(f"Schemas to introspect: {settings.schema_include}\n")
    extractor = SchemaExtractor()
    try:
        tables = extractor.extract()
    except Exception as exc:  
        print("\n--- EXTRACTION FAILED ---")
        print(f"{type(exc).__name__}: {exc}")
        sys.exit(1)

    if not tables:
        print("\nWARNING: Connected successfully, but found 0 tables in the "
              "configured schemas.")
        sys.exit(1)

    print(f"Extracted {len(tables)} tables.\n")
    graph_builder = SchemaGraphBuilder()
    graph = graph_builder.build(tables)
    cache = CacheManager(settings.cache_dir / "app_cache.sqlite3")
    cache.set_json(
        "schema_metadata",
        {qname: t.to_dict() for qname, t in tables.items()},
    )
    #pickle module to convert graphs into a raw stream of bytes, then saves it under the key "schema_graph", because graphs cannot easily be converted to simple JSON. Pickling "freezes" the exact state of the Python object so it can be restored perfectly later.
    cache.set_bytes("schema_graph", pickle.dumps(graph))
    _print_summary(tables, graph)
    print(f"\nCached to: {settings.cache_dir / 'app_cache.sqlite3'}")

def _print_summary(tables: dict, graph) -> None:
    by_schema: dict[str, int] = {}
    total_columns = 0
    total_fks = 0
    total_pks = 0
    sampled_columns = 0
    tables_with_comments = 0

    for t in tables.values():
        by_schema[t.schema] = by_schema.get(t.schema, 0) + 1
        total_columns += len(t.columns)
        total_fks += len(t.foreign_keys)
        total_pks += len(t.primary_keys)
        sampled_columns += sum(1 for c in t.columns.values() if c.sample_values)
        if t.comment:
            tables_with_comments += 1

    print("--- Summary ---")
    print(f"Tables by schema:")
    for schema, count in sorted(by_schema.items()):
        print(f"  {schema:20s} {count} tables")
    print(f"Total columns:          {total_columns}")
    print(f"Total primary keys:     {total_pks}")
    print(f"Total foreign keys:     {total_fks}")
    print(f"Columns with sampled distinct values: {sampled_columns}")
    print(f"Tables with comments:   {tables_with_comments}")
    print(f"\nGraph nodes: {graph.number_of_nodes()}  |  Graph edges: {graph.number_of_edges()}")

    # Show a couple of concrete examples so it's easy to evaluate correctness.
    print("\n--- Sample table (first one found) ---")
    first_key = next(iter(tables))
    t = tables[first_key]
    print(f"{t.qualified_name}  (~{t.row_estimate} rows)")
    print(f"  Primary keys: {t.primary_keys}")
    print(f"  Foreign keys: {[(fk.column, '->', f'{fk.ref_schema}.{fk.ref_table}.{fk.ref_column}') for fk in t.foreign_keys]}")
    for cname, col in list(t.columns.items())[:5]:
        sample = f" sample={col.sample_values[:5]}" if col.sample_values else ""
        print(f"  - {cname}: {col.data_type}{sample}")

if __name__ == "__main__":
    main()