from __future__ import annotations

"""
Built-in tools for the L3 data agent.

Provides four generalized tools with no domain-specific logic:
- RunSQLTool: execute read-only SQL queries
- SearchKnowledgeBaseTool: search local .md knowledge files
- DiscoverTablesTool: discover database tables by keyword
- GetTableSchemaTool: inspect table column definitions
"""

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from l3_agent.agent.base_tool import (
    BaseTool,
    ToolDeniedError,
    ToolRegistry,
    ToolResult,
)

if TYPE_CHECKING:
    from l3_agent.adapters.database import DatabaseAdapter


# ---------------------------------------------------------------------------
# Helper: format rows as a markdown table
# ---------------------------------------------------------------------------

def _format_markdown_table(columns: list[str], rows: list[tuple]) -> str:
    """Render columns + rows as a Markdown table string."""
    if not columns:
        return "(empty result)"

    col_widths = [len(c) for c in columns]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val)))

    def _row_line(values: list) -> str:
        cells = [str(v).ljust(w) for v, w in zip(values, col_widths)]
        return "| " + " | ".join(cells) + " |"

    header = _row_line(columns)
    separator = "| " + " | ".join("-" * w for w in col_widths) + " |"
    body = "\n".join(_row_line(row) for row in rows)
    return f"{header}\n{separator}\n{body}" if body else f"{header}\n{separator}"


# ---------------------------------------------------------------------------
# Regex pattern for detecting write operations
# ---------------------------------------------------------------------------

_WRITE_OPS_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE)\b",
    re.IGNORECASE,
)

_META_OPS_PATTERN = re.compile(
    r"^\s*(DESCRIBE|SHOW)\b",
    re.IGNORECASE,
)


# ===========================================================================
# RunSQLTool
# ===========================================================================

class RunSQLTool(BaseTool):
    """Execute a read-only SQL query against the database."""

    def __init__(self, db: DatabaseAdapter):
        self.db = db

    @property
    def name(self) -> str:
        return "run_sql"

    @property
    def description(self) -> str:
        return (
            "Execute a read-only SQL query against the database and return results "
            "as a markdown table. Write operations (INSERT, UPDATE, DELETE, DROP, "
            "CREATE, ALTER, TRUNCATE) and metadata statements (DESCRIBE, SHOW) are "
            "blocked. Use discover_tables or get_table_schema for metadata instead."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SQL query to execute (SELECT only).",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief explanation of why this query is needed.",
                },
                "is_final_data": {
                    "type": "boolean",
                    "description": (
                        "If true, the result is intended as final output and "
                        "should not be compressed."
                    ),
                },
            },
            "required": ["sql"],
        }

    is_read_only = True

    def pre_execute(self, tool_input: dict) -> dict:
        sql = tool_input["sql"]

        if _WRITE_OPS_PATTERN.search(sql):
            raise ToolDeniedError(
                "Write operations are not allowed. The database is read-only."
            )

        if _META_OPS_PATTERN.search(sql):
            raise ToolDeniedError(
                "DESCRIBE/SHOW statements are not allowed. "
                "Use discover_tables or get_table_schema instead."
            )

        return tool_input

    def execute(self, tool_input: dict) -> str:
        sql = tool_input["sql"]
        columns, rows = self.db.query_raw(sql)

        if not rows:
            return "(no rows returned)"

        table_text = _format_markdown_table(columns, rows)
        row_count = len(rows)
        return f"{table_text}\n\n({row_count} row{'s' if row_count != 1 else ''})"

    def post_execute(self, result: ToolResult) -> ToolResult:
        is_final = result.input_args.get("is_final_data", False)
        if is_final:
            return result

        content = result.content
        if len(content) > 3000:
            lines = content.split("\n")
            # Keep header (2 lines) + first 20 data rows
            kept = lines[:22]
            total_lines = len(lines)
            data_lines = total_lines - 2  # subtract header + separator
            kept_text = "\n".join(kept)
            result.content = (
                f"{kept_text}\n... (truncated)\n\n"
                f"Result too large — showing first 20 of ~{data_lines} rows. "
                f"Refine your query with WHERE/LIMIT, or set is_final_data=true "
                f"to retrieve full results."
            )
            result.metadata["truncated"] = True
            result.metadata["original_length"] = len(content)

        return result


# ===========================================================================
# SearchKnowledgeBaseTool
# ===========================================================================

class SearchKnowledgeBaseTool(BaseTool):
    """Search the local knowledge base for relevant information."""

    def __init__(self, knowledge_dir: str):
        self.knowledge_dir = knowledge_dir

    @property
    def name(self) -> str:
        return "search_knowledge_base"

    @property
    def description(self) -> str:
        return (
            "Search the local knowledge base (.md files) for domain knowledge, "
            "metric definitions, table documentation, or analytical references. "
            "Provide a domain to get a specific document, or a keyword to search "
            "across all files."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": (
                        "Exact domain/document name to retrieve "
                        "(matches against index.json keys)."
                    ),
                },
                "keyword": {
                    "type": "string",
                    "description": "Keyword to search across all knowledge files.",
                },
            },
            "required": [],
        }

    def _load_index(self) -> dict:
        """Load index.json and return a dict mapping domain -> file path."""
        index_path = os.path.join(self.knowledge_dir, "index.json")
        if not os.path.exists(index_path):
            return {}
        with open(index_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # index.json can be a list of {domain, path, annotation} or a dict
        if isinstance(raw, list):
            return {
                entry["domain"]: entry.get("path", entry["domain"] + ".md")
                for entry in raw
                if isinstance(entry, dict) and "domain" in entry
            }
        if isinstance(raw, dict):
            return raw
        return {}

    def _read_file(self, filepath: str) -> str:
        """Read a file, returning its content or an error message."""
        full_path = (
            filepath
            if os.path.isabs(filepath)
            else os.path.join(self.knowledge_dir, filepath)
        )
        if not os.path.exists(full_path):
            return f"(file not found: {filepath})"
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()

    def _grep_files(self, keyword: str) -> list[tuple[str, list[str]]]:
        """Search all .md files for lines containing the keyword."""
        results: list[tuple[str, list[str]]] = []
        kw_lower = keyword.lower()
        knowledge_path = Path(self.knowledge_dir)

        if not knowledge_path.exists():
            return results

        for md_file in sorted(knowledge_path.rglob("*.md")):
            matching_lines: list[str] = []
            try:
                text = md_file.read_text(encoding="utf-8")
                for line_num, line in enumerate(text.splitlines(), 1):
                    if kw_lower in line.lower():
                        matching_lines.append(f"  L{line_num}: {line.strip()}")
            except Exception:
                continue
            if matching_lines:
                rel = str(md_file.relative_to(knowledge_path))
                results.append((rel, matching_lines))

        return results

    def execute(self, tool_input: dict) -> str:
        domain = tool_input.get("domain", "").strip()
        keyword = tool_input.get("keyword", "").strip()

        if not domain and not keyword:
            # List available domains from index
            index = self._load_index()
            if not index:
                return "(knowledge base is empty or index.json not found)"
            listing = "\n".join(f"- **{k}**: {v}" for k, v in index.items())
            return f"Available knowledge domains:\n{listing}"

        # Domain lookup
        if domain:
            index = self._load_index()
            if domain in index:
                filepath = index[domain]
                content = self._read_file(filepath)
                return f"## {domain}\n\n{content}"

            # Fuzzy: check if domain appears as substring in any key
            matches = {
                k: v for k, v in index.items() if domain.lower() in k.lower()
            }
            if matches:
                parts = []
                for k, v in matches.items():
                    parts.append(f"## {k}\n\n{self._read_file(v)}")
                return "\n\n---\n\n".join(parts)

            return f"No knowledge document found for domain '{domain}'."

        # Keyword search
        hits = self._grep_files(keyword)
        if not hits:
            return f"No matches found for keyword '{keyword}'."

        parts = []
        for filename, lines in hits:
            snippet = "\n".join(lines[:10])
            if len(lines) > 10:
                snippet += f"\n  ... ({len(lines)} matches total)"
            parts.append(f"**{filename}**\n{snippet}")

        return "\n\n".join(parts)


# ===========================================================================
# DiscoverTablesTool
# ===========================================================================

class DiscoverTablesTool(BaseTool):
    """Discover database tables matching a keyword."""

    def __init__(self, db: DatabaseAdapter):
        self.db = db

    @property
    def name(self) -> str:
        return "discover_tables"

    @property
    def description(self) -> str:
        return (
            "List database tables whose names contain the given keyword. "
            "Use this to explore available tables before writing queries."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Keyword to filter table names (case-insensitive).",
                },
            },
            "required": ["keyword"],
        }

    def execute(self, tool_input: dict) -> str:
        keyword = tool_input["keyword"].lower()
        all_tables = self.db.get_tables()

        matches = [t for t in all_tables if keyword in t.lower()]

        if not matches:
            return (
                f"No tables found matching '{keyword}'. "
                f"Total tables in database: {len(all_tables)}."
            )

        listing = "\n".join(f"- {t}" for t in matches)
        return (
            f"Found {len(matches)} table(s) matching '{keyword}':\n{listing}"
        )


# ===========================================================================
# GetTableSchemaTool
# ===========================================================================

class GetTableSchemaTool(BaseTool):
    """Get column definitions for a specific table."""

    def __init__(self, db: DatabaseAdapter):
        self.db = db

    @property
    def name(self) -> str:
        return "get_table_schema"

    @property
    def description(self) -> str:
        return (
            "Return the column definitions (name, type, nullable, comment) "
            "for a given table. Always inspect the schema before writing queries "
            "against a table for the first time."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": (
                        "Fully qualified table name (e.g. 'schema.table' or 'table')."
                    ),
                },
            },
            "required": ["table"],
        }

    def execute(self, tool_input: dict) -> str:
        table = tool_input["table"]

        try:
            columns = self.db.get_table_schema(table)
        except Exception as e:
            return f"Failed to get schema for '{table}': {e}"

        if not columns:
            return f"Table '{table}' has no columns or does not exist."

        lines = [f"### {table}", ""]
        lines.append("| Column | Type | Nullable | Comment |")
        lines.append("| --- | --- | --- | --- |")
        for col in columns:
            comment = col.get("comment") or ""
            nullable = "YES" if col.get("nullable", True) else "NO"
            lines.append(
                f"| {col['name']} | {col['type']} | {nullable} | {comment} |"
            )

        return "\n".join(lines)


# ===========================================================================
# Factory function
# ===========================================================================

def create_default_tools(
    db: DatabaseAdapter,
    knowledge_dir: str,
) -> ToolRegistry:
    """Create a ToolRegistry with all built-in tools registered."""
    registry = ToolRegistry()
    registry.register(RunSQLTool(db))
    registry.register(SearchKnowledgeBaseTool(knowledge_dir))
    registry.register(DiscoverTablesTool(db))
    registry.register(GetTableSchemaTool(db))
    return registry
