"""
Dynamic context manager — deferred schema loading and prompt assembly.

Core idea: do NOT pack every schema / business-context fragment into the
system prompt up front.  Instead, load in layers:

  - Core layer (always present): role, analysis principles, table rules
  - On-demand layer (question-driven): relevant table schemas, playbook
  - Runtime layer (tool-discovered): additional schemas requested mid-run

This keeps the initial prompt small while guaranteeing the LLM can access
any schema it needs.
"""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

from l3_agent.config import L3Config

if TYPE_CHECKING:
    from l3_agent.adapters.database import DatabaseAdapter

log = logging.getLogger("l3_agent.context")


# ============================================================
# Knowledge index
# ============================================================

def build_knowledge_index(knowledge_dir: str) -> str:
    """Load the pre-built knowledge index from *knowledge_dir*/index.json.

    The index is generated offline (e.g. by a ``build_knowledge_index``
    script).  The agent sees the index and decides on its own whether to
    call ``search_knowledge_base`` for full content.
    """
    index_path = os.path.join(knowledge_dir, "index.json")
    if not os.path.isfile(index_path):
        return ""

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return ""

    if not data:
        return ""

    return "\n".join(f"- **{e['domain']}**: {e['annotation']}" for e in data)


# ============================================================
# Schema group helpers
# ============================================================

def detect_relevant_groups(
    question: str,
    context: str,
    schema_groups: dict[str, Any],
) -> list[str]:
    """Return schema-group names whose trigger keywords match the question/context."""
    text = (question + " " + context).lower()
    matched: list[str] = []

    for group_name, group_info in schema_groups.items():
        triggers = group_info.get("triggers", [])
        if isinstance(triggers, list):
            trigger_list = triggers
        else:
            trigger_list = getattr(triggers, "__iter__", lambda: [])()
        for trigger in trigger_list:
            if trigger.lower() in text:
                matched.append(group_name)
                break

    return list(set(matched))


def extract_schema_for_groups(
    groups: list[str],
    schema_groups: dict[str, Any],
    db: "DatabaseAdapter",
) -> str:
    """Build a schema text block for the requested groups.

    Uses ``db.get_table_schema_text()`` to fetch each table's schema
    dynamically rather than relying on a hardcoded cache.
    """
    needed_tables: set[str] = set()
    for group_name in groups:
        group = schema_groups.get(group_name)
        if group:
            tables = group.get("tables", [])
            if isinstance(tables, list):
                needed_tables.update(tables)
            else:
                needed_tables.update(getattr(tables, "__iter__", lambda: [])())

    if not needed_tables:
        return ""

    schema_parts: list[str] = []
    for table in sorted(needed_tables):
        try:
            schema_parts.append(db.get_table_schema_text(table))
        except Exception as exc:
            log.warning("Failed to load schema for %s: %s", table, exc)

    loaded_labels = ", ".join(
        schema_groups[g].get("display", g)
        for g in groups
        if g in schema_groups
    )
    header = (
        f"## Data Warehouse — Relevant Tables (loaded on demand)\n"
        f"Loaded: {loaded_labels}\n"
        f"If you need other tables, describe what data you need and the system "
        f"will load the corresponding schema.\n\n"
    )
    return header + "\n\n".join(schema_parts)


def _auto_discover_schema(db: "DatabaseAdapter") -> str:
    """Fallback: when no schema_groups are configured, pull every table from
    the database and present them as a single block.
    """
    try:
        tables = db.get_tables()
    except Exception as exc:
        log.warning("Auto-discover failed: %s", exc)
        return ""

    if not tables:
        return ""

    parts: list[str] = []
    for table in tables:
        try:
            parts.append(db.get_table_schema_text(table))
        except Exception:
            parts.append(f"### {table}\n  (schema unavailable)")

    header = (
        "## Data Warehouse — All Tables (auto-discovered)\n\n"
    )
    return header + "\n\n".join(parts)


# ============================================================
# System prompt template
# ============================================================

CORE_SYSTEM_PROMPT = """You are {role}

Today is {today}.

{schema}

{table_rules}

{analysis_principles}

## Your Approach

### Core Principle: Always Ask "Why"
When you find a metric change, don't stop at "A is 3% higher than B". Investigate:
- WHY did this happen? What's the mechanism?
- Is it because of the change itself, or population composition?
- If you remove confounders, does the conclusion still hold?

### Before Analysis: Choose dimensions by problem nature
Don't mechanically slice by every dimension. Think: what dimensions are most relevant?

### During Analysis: Signal vs Noise
- Small samples (<500) → don't conclude, mark "insufficient sample"
- Check if differences are statistically significant

### After Analysis: Three-Layer Answer
1. What happened? (numbers — specific pp/percentages/absolute values)
2. Why? (mechanism — which user behavior changed? how did it propagate?)
3. What does it mean? (business — what should we do?)

### Analysis Discipline
- Don't write all SQL at once — each result may change direction
- Interpret results before next query
- Check anomalous results for SQL bugs before trusting
- Numbers must be specific, comparisons need baselines

## Metadata Discovery Tools
- Can't find a table → `discover_tables(keyword="...")`
- Need column names → `get_table_schema(table="...")`

## Knowledge Base
{knowledge_index}

{playbook}

{extra_context}"""


# ============================================================
# ContextManager
# ============================================================

class ContextManager:
    """Dynamic context manager.

    Responsibilities:
    1. Detect which schema groups are relevant based on the question
    2. Inject playbook text on demand
    3. Manage runtime context additions (supplementary schema loads)
    4. Track loaded context to avoid duplication
    """

    def __init__(self, config: L3Config, db: "DatabaseAdapter"):
        self._config = config
        self._db = db
        self._loaded_groups: set[str] = set()
        self._extra_contexts: list[str] = []

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    def build_system_prompt(
        self,
        question: str,
        today: str,
        context: str = "",
        playbook_text: str = "",
    ) -> str:
        """Assemble the full system prompt.

        Parameters
        ----------
        question:
            The user's analysis question (used to select relevant schema groups).
        today:
            Human-readable date string, e.g. ``"2025-06-15"``.
        context:
            Optional extra context supplied by the caller.
        playbook_text:
            Optional playbook content to inject.
        """
        ctx = self._config.context
        schema_groups = {
            name: grp.model_dump() if hasattr(grp, "model_dump") else grp
            for name, grp in ctx.schema_groups.items()
        }

        # --- Schema ---
        if schema_groups:
            groups = detect_relevant_groups(question, context, schema_groups)
            # If nothing matched, load the first two groups as sensible defaults
            if not groups:
                groups = list(schema_groups.keys())[:2]
            self._loaded_groups.update(groups)
            schema_text = extract_schema_for_groups(groups, schema_groups, self._db)
        else:
            # Auto-discover mode: no groups configured, pull everything from DB
            schema_text = _auto_discover_schema(self._db)

        # --- Knowledge index ---
        knowledge_dir = self._config.knowledge.directory
        knowledge_index = build_knowledge_index(knowledge_dir)

        # --- Extra context ---
        extra_parts: list[str] = []
        if context:
            extra_parts.append(f"## Supplementary Context\n{context}")
        for ec in self._extra_contexts:
            extra_parts.append(ec)
        extra_context = "\n\n".join(extra_parts)

        return CORE_SYSTEM_PROMPT.format(
            role=ctx.role,
            today=today,
            schema=schema_text,
            table_rules=ctx.table_rules,
            analysis_principles=ctx.analysis_principles,
            knowledge_index=knowledge_index,
            playbook=playbook_text,
            extra_context=extra_context,
        )

    def request_additional_schema(self, sql_text: str) -> str | None:
        """Request extra schema at runtime.

        Call this when the LLM's SQL references tables not yet loaded.
        Returns the new schema text, or ``None`` if everything is already
        loaded.
        """
        ctx = self._config.context
        schema_groups = {
            name: grp.model_dump() if hasattr(grp, "model_dump") else grp
            for name, grp in ctx.schema_groups.items()
        }

        if not schema_groups:
            return None

        new_groups = detect_relevant_groups(sql_text, "", schema_groups)
        unloaded = [g for g in new_groups if g not in self._loaded_groups]

        if not unloaded:
            return None

        self._loaded_groups.update(unloaded)
        additional = extract_schema_for_groups(unloaded, schema_groups, self._db)
        return f"\n## Supplementary Schema (loaded on demand)\n{additional}"

    @property
    def loaded_groups(self) -> list[str]:
        """Return a sorted list of currently loaded schema-group names."""
        return sorted(self._loaded_groups)
