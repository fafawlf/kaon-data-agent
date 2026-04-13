"""Database adapter — SQLAlchemy-based, supports SQLite/PostgreSQL/MySQL/DuckDB."""
from __future__ import annotations

from typing import Protocol, Any

import pandas as pd
from sqlalchemy import create_engine, inspect, text


class DatabaseAdapter(Protocol):
    """Protocol for database access."""

    def query(self, sql: str) -> pd.DataFrame: ...
    def query_raw(self, sql: str) -> tuple[list[str], list[tuple]]: ...
    def get_tables(self) -> list[str]: ...
    def get_table_schema(self, table: str) -> list[dict[str, Any]]: ...


class SQLAlchemyAdapter:
    """Universal database adapter using SQLAlchemy."""

    def __init__(self, connection_string: str):
        self.engine = create_engine(connection_string)
        self._connection_string = connection_string

    def query(self, sql: str) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(text(sql), conn)

    def query_raw(self, sql: str) -> tuple[list[str], list[tuple]]:
        with self.engine.connect() as conn:
            result = conn.execute(text(sql))
            columns = list(result.keys())
            rows = [tuple(row) for row in result.fetchall()]
            return columns, rows

    def get_tables(self) -> list[str]:
        insp = inspect(self.engine)
        tables = []
        for schema_name in insp.get_schema_names():
            for table_name in insp.get_table_names(schema=schema_name):
                if schema_name and schema_name != "main":
                    tables.append(f"{schema_name}.{table_name}")
                else:
                    tables.append(table_name)
        return sorted(tables)

    def get_table_schema(self, table: str) -> list[dict[str, Any]]:
        insp = inspect(self.engine)
        schema_name = None
        table_name = table
        if "." in table:
            schema_name, table_name = table.rsplit(".", 1)
            if schema_name == "main":
                schema_name = None

        columns = []
        for col in insp.get_columns(table_name, schema=schema_name):
            columns.append({
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col.get("nullable", True),
                "comment": col.get("comment", ""),
            })
        return columns

    def get_table_schema_text(self, table: str) -> str:
        columns = self.get_table_schema(table)
        lines = [f"### {table}"]
        for col in columns:
            comment = f" -- {col['comment']}" if col.get("comment") else ""
            lines.append(f"  - {col['name']}: {col['type']}{comment}")
        return "\n".join(lines)
