"""
Context and result compressors.

ContextCompressor: summarise-and-collapse when the message history exceeds
a token budget.  Preserves head/tail messages, replaces the middle with an
LLM-generated summary, and repairs orphaned tool_use / tool_result pairs.

CompressedResult + compress_result: large tool outputs are saved to disk
and replaced with a stats-plus-preview summary so the LLM stays within
its context window.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from l3_agent.adapters.llm import LLMAdapter

log = logging.getLogger("l3_agent.compressors")

# ============================================================
# Constants
# ============================================================

CHARS_PER_TOKEN = 4
MIN_MESSAGES_FOR_COMPRESS = 4  # need at least this many between head and tail

SUMMARY_PROMPT = """Summarize the following data-analysis conversation, preserving:
1. SQL queries executed and key result numbers
2. Hypotheses verified or ruled out
3. Key trends and anomalies discovered
4. Table names and important columns referenced

Output a concise summary, no more than {budget} characters.  Return only the
summary text — no preamble, no explanation.

---
{turns_text}
"""


# ============================================================
# Token estimation
# ============================================================

def estimate_tokens_rough(
    messages: List[Dict[str, Any]],
    system_prompt: str = "",
    tools_schema: Optional[list] = None,
) -> int:
    """Rough token estimate: total chars / CHARS_PER_TOKEN.

    Includes system prompt and tools-schema overhead.
    """
    total_chars = 0
    if system_prompt:
        total_chars += len(system_prompt)
    if messages:
        total_chars += sum(len(str(msg)) for msg in messages)
    if tools_schema:
        total_chars += len(str(tools_schema))
    return total_chars // CHARS_PER_TOKEN


# ============================================================
# ContextCompressor
# ============================================================

class ContextCompressor:
    """Compress the message history when it approaches the context-window limit.

    Strategy: keep head and tail messages intact, replace the middle with an
    LLM-generated summary, then repair any orphaned tool_use / tool_result
    pairs.
    """

    def __init__(
        self,
        llm: LLMAdapter,
        context_window: int = 200_000,
        threshold_percent: float = 0.50,
        protect_first_n: int = 3,
        protect_last_n: int = 20,
        summary_ratio: float = 0.20,
    ):
        self.llm = llm
        self.context_window = context_window
        self.threshold_percent = threshold_percent
        self.threshold_tokens = int(context_window * threshold_percent)
        self.protect_first_n = protect_first_n
        self.protect_last_n = protect_last_n
        self.summary_ratio = summary_ratio

    # --------------------------------------------------------
    # Trigger check
    # --------------------------------------------------------

    def should_compress(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str = "",
        tools_schema: Optional[list] = None,
    ) -> bool:
        """Return True if the conversation should be compressed."""
        min_msgs = self.protect_first_n + self.protect_last_n + MIN_MESSAGES_FOR_COMPRESS
        if len(messages) < min_msgs:
            return False
        estimated = estimate_tokens_rough(messages, system_prompt, tools_schema)
        return estimated >= self.threshold_tokens

    # --------------------------------------------------------
    # Main compression logic
    # --------------------------------------------------------

    def compress(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str = "",
    ) -> List[Dict[str, Any]]:
        """Compress message history: keep head/tail, replace middle with summary."""
        min_msgs = self.protect_first_n + self.protect_last_n + MIN_MESSAGES_FOR_COMPRESS
        if len(messages) < min_msgs:
            return messages

        messages = copy.deepcopy(messages)
        head_end = self.protect_first_n
        tail_start = self._find_tail_boundary(messages, head_end)

        # Align boundaries to avoid splitting tool_use + tool_result pairs
        head_end = self._align_for_tool_groups(messages, head_end)
        tail_start = self._align_for_tool_groups(messages, tail_start)

        # Nothing compressible in the middle
        if tail_start <= head_end + 2:
            return messages

        # Serialise middle messages and generate a summary
        middle = messages[head_end:tail_start]
        turns_text = self._serialize_turns(middle)

        summary = self._generate_summary(turns_text)
        if not summary:
            log.warning("compress.summary_failed")
            return messages

        # Build summary message (user role so it follows any trailing assistant message)
        summary_msg = {
            "role": "user",
            "content": (
                "[Context compression summary] "
                "Below is a summary of the preceding analysis conversation:\n\n"
                f"{summary}"
            ),
        }

        # Assemble result
        result = messages[:head_end] + [summary_msg] + messages[tail_start:]

        # Fix orphaned tool pairs
        result = self._sanitize_tool_pairs(result)

        log.info(
            "compress.done  original_msgs=%d  compressed_msgs=%d  middle_removed=%d",
            len(messages), len(result), len(middle),
        )
        return result

    # --------------------------------------------------------
    # Tail boundary
    # --------------------------------------------------------

    def _find_tail_boundary(self, messages: List[Dict], head_end: int) -> int:
        """Walk backward from the end, accumulating tokens until the tail
        budget is reached.  Protect at least ``protect_last_n`` messages.
        """
        tail_budget = int(self.threshold_tokens * self.summary_ratio)
        accumulated = 0
        boundary = len(messages)

        for i in range(len(messages) - 1, head_end, -1):
            msg_tokens = len(str(messages[i])) // CHARS_PER_TOKEN
            accumulated += msg_tokens
            boundary = i
            if accumulated >= tail_budget:
                break

        max_boundary = len(messages) - self.protect_last_n
        if boundary > max_boundary:
            boundary = max_boundary

        return max(boundary, head_end + 2)

    # --------------------------------------------------------
    # Boundary alignment
    # --------------------------------------------------------

    def _align_for_tool_groups(self, messages: List[Dict], idx: int) -> int:
        """Adjust a boundary so it does not split a tool_use + tool_result pair.

        If *idx* points at a user(tool_result) message, move it back one
        position to include the matching assistant(tool_use).
        """
        if idx <= 0 or idx >= len(messages):
            return idx

        msg = messages[idx]

        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            has_tool_result = any(
                b.get("type") == "tool_result"
                for b in msg["content"]
                if isinstance(b, dict)
            )
            if has_tool_result and idx > 0:
                prev = messages[idx - 1]
                if prev["role"] == "assistant" and isinstance(prev.get("content"), list):
                    has_tool_use = any(
                        b.get("type") == "tool_use"
                        for b in prev["content"]
                        if isinstance(b, dict)
                    )
                    if has_tool_use:
                        return idx - 1

        return idx

    # --------------------------------------------------------
    # Summary generation
    # --------------------------------------------------------

    def _generate_summary(self, turns_text: str) -> Optional[str]:
        """Call the LLM to produce a conversation summary."""
        if not self.llm:
            return None

        content_tokens = len(turns_text) // CHARS_PER_TOKEN
        budget_chars = max(2000, int(content_tokens * self.summary_ratio * CHARS_PER_TOKEN))
        budget_chars = min(budget_chars, 12000)

        prompt = SUMMARY_PROMPT.format(budget=budget_chars, turns_text=turns_text)

        try:
            resp = self.llm.create_message(
                system="You are a data-analysis conversation summarizer. Produce a concise, precise summary.",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                temperature=0,
            )
            # resp is a normalized dict with content blocks
            for block in resp.get("content", []):
                if block.get("type") == "text":
                    return block["text"].strip()
            return None
        except Exception as e:
            log.error("compress.summary_error  error=%s", str(e))
            return None

    # --------------------------------------------------------
    # Message serialisation
    # --------------------------------------------------------

    def _serialize_turns(self, messages: List[Dict]) -> str:
        """Serialize messages into plain text for the summarizer."""
        parts: list[str] = []
        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            if isinstance(content, str):
                parts.append(f"[{role}] {content[:3000]}")
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    if btype == "text":
                        parts.append(f"[{role}] {block.get('text', '')[:3000]}")
                    elif btype == "tool_use":
                        name = block.get("name", "")
                        inp = str(block.get("input", ""))[:500]
                        parts.append(f"[{role}:tool_call] {name}({inp})")
                    elif btype == "tool_result":
                        result_text = block.get("content", "")
                        if len(result_text) > 2000:
                            result_text = result_text[:1500] + "\n...\n" + result_text[-500:]
                        parts.append(f"[tool_result] {result_text}")

        return "\n".join(parts)

    # --------------------------------------------------------
    # Tool-pair repair
    # --------------------------------------------------------

    def _sanitize_tool_pairs(self, messages: List[Dict]) -> List[Dict]:
        """Repair orphaned tool_use / tool_result blocks after compression.

        Anthropic format:
        - tool_use blocks live inside assistant messages
        - tool_result blocks live inside user messages
        """
        messages = copy.deepcopy(messages)

        # Collect all tool_use ids and tool_result ids
        tool_use_ids: set[str] = set()
        tool_result_ids: set[str] = set()

        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    tool_use_ids.add(block.get("id"))
                elif block.get("type") == "tool_result":
                    tool_result_ids.add(block.get("tool_use_id"))

        # 1. Remove orphaned tool_result blocks (no matching tool_use)
        orphan_result_ids = tool_result_ids - tool_use_ids
        if orphan_result_ids:
            for msg in messages:
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                msg["content"] = [
                    b for b in content
                    if not (
                        isinstance(b, dict)
                        and b.get("type") == "tool_result"
                        and b.get("tool_use_id") in orphan_result_ids
                    )
                ]

        # 2. Insert stub results for orphaned tool_use blocks (no matching tool_result)
        orphan_use_ids = tool_use_ids - tool_result_ids
        if orphan_use_ids:
            inserts: list[tuple[int, dict]] = []
            for i, msg in enumerate(messages):
                if msg["role"] != "assistant":
                    continue
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                orphans_in_msg = [
                    b.get("id")
                    for b in content
                    if isinstance(b, dict)
                    and b.get("type") == "tool_use"
                    and b.get("id") in orphan_use_ids
                ]
                if orphans_in_msg:
                    stub_blocks = [
                        {
                            "type": "tool_result",
                            "tool_use_id": tid,
                            "content": "[Result removed during context compression — see summary above]",
                        }
                        for tid in orphans_in_msg
                    ]
                    # If the next message is a user message with tool_results, append there
                    if i + 1 < len(messages) and messages[i + 1]["role"] == "user":
                        next_content = messages[i + 1].get("content")
                        if isinstance(next_content, list) and any(
                            isinstance(b, dict) and b.get("type") == "tool_result"
                            for b in next_content
                        ):
                            messages[i + 1]["content"].extend(stub_blocks)
                            continue
                    inserts.append((i + 1, {"role": "user", "content": stub_blocks}))

            for idx, stub_msg in reversed(inserts):
                messages.insert(idx, stub_msg)

        # 3. Remove messages with empty content
        messages = [
            m for m in messages
            if m.get("content") and (
                not isinstance(m["content"], list) or len(m["content"]) > 0
            )
        ]

        return messages


# ============================================================
# Result compression (large tool outputs)
# ============================================================

COMPRESS_THRESHOLD_CHARS = 3000   # trigger compression above this length
MAX_PREVIEW_ROWS = 20             # rows kept in the preview
DEFAULT_STORAGE_DIR = os.path.join(".", "output", "query_results")


@dataclass
class CompressedResult:
    """Wrapper returned by ``compress_result``."""
    summary: str          # truncated preview for the LLM
    full_content: str     # complete original content
    was_compressed: bool  # whether compression was applied
    file_path: str = ""   # path to the saved full output (if any)


def compress_result(
    content: str,
    tool_name: str = "run_sql",
    sql: str = "",
    reason: str = "",
    storage_dir: str = DEFAULT_STORAGE_DIR,
) -> CompressedResult:
    """Intelligently compress a tool result.

    Short results are returned unchanged.  Long results get a stats header
    plus a row preview; the full content is saved to *storage_dir*.
    """
    if len(content) <= COMPRESS_THRESHOLD_CHARS:
        return CompressedResult(
            summary=content,
            full_content=content,
            was_compressed=False,
        )

    lines = content.strip().split("\n")
    total_lines = len(lines)

    stats = _extract_stats(lines)

    if total_lines > MAX_PREVIEW_ROWS + 1:
        preview_lines = lines[: MAX_PREVIEW_ROWS + 1]  # +1 for header
        preview = "\n".join(preview_lines)
    else:
        preview = content

    file_path = _save_to_file(content, tool_name, sql, reason, storage_dir)

    summary_parts: list[str] = []
    if stats:
        summary_parts.append(f"[Result stats: {total_lines} rows, {len(content)} chars]")
        summary_parts.append(stats)
    summary_parts.append(preview)
    if total_lines > MAX_PREVIEW_ROWS + 1:
        summary_parts.append(
            f"\n... {total_lines - MAX_PREVIEW_ROWS - 1} rows omitted. "
            f"Full result saved. Aggregate in SQL if you need more data."
        )

    return CompressedResult(
        summary="\n".join(summary_parts),
        full_content=content,
        was_compressed=True,
        file_path=file_path,
    )


def _extract_stats(lines: list[str]) -> str:
    """Extract basic stats from tabular output."""
    if len(lines) < 2:
        return ""
    header = lines[0]
    data_lines = lines[1:]
    col_count = len(header.split())
    row_count = len(data_lines)
    return f"Columns: {col_count}, Data rows: {row_count}"


def _save_to_file(
    content: str,
    tool_name: str,
    sql: str,
    reason: str,
    storage_dir: str,
) -> str:
    """Persist the full result to a text file and return its path."""
    os.makedirs(storage_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
    filename = f"{tool_name}_{timestamp}_{content_hash}.txt"
    filepath = os.path.join(storage_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# Tool: {tool_name}\n")
        f.write(f"# Time: {timestamp}\n")
        if reason:
            f.write(f"# Reason: {reason}\n")
        if sql:
            f.write(f"# SQL:\n# {sql}\n")
        f.write(f"# {'=' * 60}\n\n")
        f.write(content)

    return filepath
