""" Plain dataclasses for extracted schema metadata.
Kept dependency-free (no pydantic) since these get pickled/JSON-serialized
into the local cache constantly and we want that fast and simple.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class ForeignKeyInfo:
    column: str
    ref_schema: str
    ref_table: str
    ref_column: str
    constraint_name: str


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    is_nullable: bool
    ordinal_position: int
    is_primary_key: bool = False
    is_foreign_key: bool = False
    comment: Optional[str] = None
    sample_values: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "is_nullable": self.is_nullable,
            "ordinal_position": self.ordinal_position,
            "is_primary_key": self.is_primary_key,
            "is_foreign_key": self.is_foreign_key,
            "comment": self.comment,
            "sample_values": self.sample_values,
        }


@dataclass
class TableInfo:
    schema: str
    name: str
    columns: dict[str, ColumnInfo] = field(default_factory=dict)
    primary_keys: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = field(default_factory=list)
    comment: Optional[str] = None
    row_estimate: Optional[int] = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "comment": self.comment,
            "row_estimate": self.row_estimate,
            "primary_keys": self.primary_keys,
            "foreign_keys": [fk.__dict__ for fk in self.foreign_keys],
            "columns": {cname: col.to_dict() for cname, col in self.columns.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TableInfo":
        table = cls(
            schema=data["schema"],
            name=data["name"],
            comment=data.get("comment"),
            row_estimate=data.get("row_estimate"),
            primary_keys=data.get("primary_keys", []),
            foreign_keys=[ForeignKeyInfo(**fk) for fk in data.get("foreign_keys", [])],
        )
        for cname, cdata in data.get("columns", {}).items():
            table.columns[cname] = ColumnInfo(**cdata)
        return table
