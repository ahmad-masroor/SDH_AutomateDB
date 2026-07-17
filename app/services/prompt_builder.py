""" Prompt builder.
Takes the table shortlist the retriever already narrowed things down to,
and the schema graph, and assembles one focused prompt for the SQL
generating model: table and column definitions (including sampled real
values, so the model can match "engineers" against the actual stored
string "Design Engineer"), the join relationships connecting those
tables, the dialect, and the question itself.
On a retry, the previous attempt's SQL and the real database error get
appended, so the model is correcting a concrete, named mistake rather
than guessing again from scratch.
"""
from __future__ import annotations
from app.models.schema_models import TableInfo
 
class PromptBuilder:
    def __init__(self, dialect: str = "PostgreSQL", max_rows: int = 500):
        self.dialect = dialect
        self.max_rows = max_rows
 
    def build(
        self,
        question: str,
        tables: dict[str, TableInfo],
        table_names: list[str],
        graph,
        retry_sql: str | None = None,
        retry_error: str | None = None,
    ) -> str:
        schema_section = "\n\n".join(
            self._describe_table(tables[name]) for name in table_names if name in tables
        )
        join_section = self._describe_joins(graph, table_names)
 
        prompt = f"""You are a careful {self.dialect} PostGreSQL query writer working against a real
production database. Write exactly one read-only SQL statement (SELECT
only) that answers the question below, using only the tables and columns
listed. Never invent a table or column name that is not listed below.
Include a LIMIT of at most {self.max_rows} unless the question clearly asks
for a single aggregate value. When a question mentions a value in plain
English (e.g. "engineers"), match it against the closest real sampled
value shown below (e.g. "Design Engineer"), not the plain English phrase
itself. The person asking the question does not know this database's table or
column names, so never return a bare foreign key id column (anything
ending in "id" that is marked as a foreign key below) when the question
asks for what that id identifies in plain language, such as a "name",
who someone "is", or which department, vendor, category, or person is
involved. In that case, always join to the referenced table (see the
join relationships below) and return its descriptive column (typically
named name, title, or description) instead of, or in addition to, the
raw id. Only return a bare id column if the question explicitly asks for
an id/number specifically, or no descriptive column exists on the
referenced table.
Relevant tables:
{schema_section} 
Known join relationships:
{join_section} 
Question: {question}
"""
        if retry_sql and retry_error:
            prompt += f""" Your previous attempt was: {retry_sql} 
                        That statement failed when actually executed against the database, with
                        this error:
                        {retry_error}
                        Write a corrected statement that avoids this exact error. Do not repeat
                        the same mistake.
                        """
        return prompt
 
    @staticmethod
    def _describe_table(table: TableInfo) -> str:
        lines = [f"Table {table.qualified_name}"]
        if table.comment:
            lines.append(f"  -- {table.comment}")
        for col_name, col in table.columns.items():
            tags = []
            if col.is_primary_key:
                tags.append("primary key")
            if col.is_foreign_key:
                tags.append("foreign key")
            if not col.is_nullable:
                tags.append("not null")
            tag_str = f" ({', '.join(tags)})" if tags else ""
            comment_str = f" -- {col.comment}" if col.comment else ""
            sample_str = ""
            if col.sample_values:
                shown = ", ".join(col.sample_values[:8])
                sample_str = f" [example values: {shown}]"
            lines.append(f"  {col_name}: {col.data_type}{tag_str}{comment_str}{sample_str}")
        return "\n".join(lines)
 
    @staticmethod
    def _describe_joins(graph, table_names: list[str]) -> str:
        """  Walks the column-level 'references' edges in the schema graph and
        renders any join path connecting two tables that are both in this
        prompt's table set. Uses the real graph rather than re-deriving
        relationships, so this always matches what expand_related_tables
        actually used to build the table set in the first place.
        """
        described: list[str] = []
        seen = set()
        table_set = set(table_names)
 
        for node, data in graph.nodes(data=True):
            if data.get("node_type") != "column":
                continue
            for _, target, edge_data in graph.out_edges(node, data=True):
                if edge_data.get("kind") != "references":
                    continue
                src_table = data.get("table")
                dst_table = graph.nodes[target].get("table")
                if src_table not in table_set or dst_table not in table_set:
                    continue
                edge_key = tuple(sorted([node, target]))
                if edge_key in seen:
                    continue
                seen.add(edge_key)
                src_col = graph.nodes[node].get("name")
                dst_col = graph.nodes[target].get("name")
                described.append(f"  {src_table}.{src_col} -> {dst_table}.{dst_col}")
 
        return "\n".join(described) if described else "  (no joins needed for these tables)"