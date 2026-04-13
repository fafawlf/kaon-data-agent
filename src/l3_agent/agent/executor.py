"""
L3Agent executor — generalized autonomous data analysis agent.

Architecture:
1. Unified Tool abstraction — BaseTool + ToolRegistry, PreToolUse/PostToolUse hooks
2. Dynamic context management — ContextManager loads schemas on demand
3. Query result compression — large results saved to disk, LLM sees summaries
4. Planning mode — structured hypothesis-driven plan before execution
5. Runtime schema supplementation — SQL referencing unloaded tables triggers auto-load
6. Playbook system — domain-specific analytical frameworks guide the agent

The analyze() entry point follows this flow:
  build_system_prompt -> optional_planning -> agentic_tool_loop -> confidence_assessment
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Iterator

from l3_agent.config import L3Config
from l3_agent.adapters.database import SQLAlchemyAdapter, DatabaseAdapter
from l3_agent.adapters.llm import LLMAdapter
from l3_agent.tools.builtin import create_default_tools
from l3_agent.agent.base_tool import ToolResult, ToolRegistry
from l3_agent.context.manager import ContextManager
from l3_agent.agent.planner import build_planning_prompt, format_plan_for_injection
from l3_agent.agent.compressors import ContextCompressor, estimate_tokens_rough
from l3_agent.playbooks.base import create_default_playbook_registry

log = logging.getLogger("l3_agent.executor")


# ============================================================
# Parallel-safe tools
# ============================================================

PARALLEL_SAFE_TOOLS = frozenset({
    "discover_tables",
    "get_table_schema",
    "search_knowledge_base",
})

_MAX_PARALLEL_WORKERS = 5


# ============================================================
# Confidence assessment prompt
# ============================================================

CONFIDENCE_PROMPT = """Based on all the query results and analysis above, perform a self-assessment of your answer.

Output the following JSON directly (no other text, no code-block markers):
{{"confidence": "high or medium or low", "confidence_reason": "brief explanation", "ruled_out": ["ruled-out hypothesis 1", "ruled-out hypothesis 2"], "evidence_summary": "one-sentence summary of the core evidence chain"}}

Confidence criteria:
- high: data is clear and consistent, cross-validated from multiple angles, conclusions supported by statistical significance
- medium: main conclusions are data-backed, but some aspects lack verification or are at borderline significance
- low: insufficient or contradictory data, conclusions are not sufficiently certain"""


# ============================================================
# L3Agent
# ============================================================

class L3Agent:
    """Autonomous data analysis agent.

    Features:
    - enable_planning: generate a structured plan before analysis (default True)
    - enable_compression: compress large tool outputs (default True)
    - enable_context_compression: summarize message history when approaching
      the context-window limit (default True)
    - enable_parallel_tools: execute safe tools in parallel (default True)
    """

    def __init__(
        self,
        config: L3Config,
        db: DatabaseAdapter = None,
        llm: LLMAdapter = None,
        tools: ToolRegistry = None,
        context_manager: ContextManager = None,
        playbook_registry=None,
    ):
        self._config = config

        # --- LLM adapter ---
        if llm is not None:
            self.llm = llm
        else:
            llm_cfg = config.llm
            self.llm = LLMAdapter(
                provider=llm_cfg.provider,
                model=llm_cfg.model,
                api_key=llm_cfg.resolve_api_key(),
                temperature=llm_cfg.temperature,
                max_output_tokens=llm_cfg.max_output_tokens,
            )

        # --- Database adapter ---
        if db is not None:
            self.db = db
        else:
            self.db = SQLAlchemyAdapter(config.database.connection_string)

        # --- Context manager ---
        if context_manager is not None:
            self.context_manager = context_manager
        else:
            self.context_manager = ContextManager(config, self.db)

        # --- Tool registry ---
        if tools is not None:
            self.tool_registry: ToolRegistry = tools
        else:
            knowledge_dir = config.knowledge.directory
            self.tool_registry: ToolRegistry = create_default_tools(self.db, knowledge_dir)

        # --- Playbook registry ---
        if playbook_registry is not None:
            self.playbook_registry = playbook_registry
        else:
            playbook_dir = config.playbooks.directory or None
            self.playbook_registry = create_default_playbook_registry(playbook_dir)

        # --- Context compressor ---
        self._compressor = ContextCompressor(llm=self.llm)

        # --- Agent settings ---
        agent_cfg = config.agent
        self.max_rounds = agent_cfg.max_rounds
        self.enable_planning = agent_cfg.enable_planning
        self.enable_compression = agent_cfg.enable_compression
        self.enable_context_compression = agent_cfg.enable_context_compression
        self.enable_parallel_tools = agent_cfg.enable_parallel_tools

    # ============================================================
    # Main entry point
    # ============================================================

    def analyze(
        self,
        question: str,
        context: str = "",
        conversation_id: str = None,
        today: str = None,
        playbook: str = None,
        image_blocks: list[dict] = None,
    ) -> dict:
        """Answer an arbitrary data question by autonomously querying the database.

        Parameters
        ----------
        question:
            The analysis question (plain text).
        context:
            Supplementary context for the analysis.
        conversation_id:
            Tracking identifier; auto-generated if not provided.
        today:
            Reference date (YYYY-MM-DD).  Defaults to yesterday.
        playbook:
            Explicit playbook name.  If ``None``, auto-detect from question.
        image_blocks:
            Optional image content blocks for multi-modal input.

        Returns
        -------
        dict with keys: answer, confidence, confidence_reason, queries,
        ruled_out, evidence_summary, conversation_id, plan, tool_stats,
        loaded_schema_groups.
        """
        conv_id = conversation_id or str(uuid.uuid4())

        if today is None:
            today = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # === 1. Resolve playbook ===
        playbook_text = ""
        if playbook:
            pb = self.playbook_registry.get(playbook)
            if pb:
                playbook_text = pb.content
        else:
            pb = self.playbook_registry.detect_playbook(question)
            if pb:
                playbook_text = pb.content
                log.info("playbook.auto_detected name=%s", pb.name)

        # === 2. Build system prompt (dynamic context) ===
        system_prompt = self.context_manager.build_system_prompt(
            question=question,
            today=today,
            context=context,
            playbook_text=playbook_text,
        )

        # === 3. Planning mode ===
        plan = None
        plan_text = ""
        if self.enable_planning:
            plan = self._generate_plan(question, context, system_prompt)
            if plan:
                plan_text = format_plan_for_injection(plan)
                log.info("plan.generated title=%s estimated_queries=%s",
                         plan.get("plan_title", "?"),
                         plan.get("estimated_queries", "?"))

        # === 4. Build initial message (supports multi-modal) ===
        text_content = question
        if plan_text:
            text_content = f"{question}\n\n{plan_text}"

        if image_blocks:
            user_content = list(image_blocks)
            user_content.append({"type": "text", "text": text_content})
        else:
            user_content = text_content

        messages: list[dict] = [{"role": "user", "content": user_content}]
        queries_log: list[dict] = []
        tool_stats = {"total_calls": 0, "denied": 0, "compressed": 0, "errors": 0}

        log.info("analysis.start question=%s conversation_id=%s",
                 question[:80], conv_id)

        # === 5. Main loop: tool-use agentic investigation ===
        tools_schema = self.tool_registry.get_claude_tools()

        for round_num in range(self.max_rounds + 2):
            # --- Context compression check ---
            if self.enable_context_compression:
                if self._compressor.should_compress(messages, system_prompt, tools_schema):
                    est_tokens = estimate_tokens_rough(messages, system_prompt, tools_schema)
                    log.info("context.compressing estimated_tokens=%d round=%d",
                             est_tokens, round_num)
                    messages = self._compressor.compress(messages, system_prompt)

            # --- LLM call ---
            try:
                resp = self.llm.stream_message(
                    system=system_prompt,
                    messages=messages,
                    tools=tools_schema,
                    max_tokens=self.llm.max_output_tokens,
                    temperature=0,
                )
            except Exception as e:
                # One fallback retry for transient errors
                if self._is_retryable(e):
                    import time
                    delay = 30
                    log.warning("api.fallback_retry error=%s delay=%ds",
                                str(e)[:120], delay)
                    time.sleep(delay)
                    try:
                        resp = self.llm.stream_message(
                            system=system_prompt,
                            messages=messages,
                            tools=tools_schema,
                            max_tokens=self.llm.max_output_tokens,
                            temperature=0,
                        )
                    except Exception as e2:
                        log.error("api.retry_exhausted error=%s", str(e2))
                        return self._error_result(
                            f"API call failed: {e2}", conv_id, queries_log
                        )
                else:
                    log.error("api.non_retryable error=%s", str(e))
                    return self._error_result(
                        f"API call failed: {e}", conv_id, queries_log
                    )

            # --- LLM gave a final answer ---
            if resp["stop_reason"] == "end_turn":
                final_text = self._extract_text(resp["content"])
                log.info(
                    "analysis.complete total_calls=%d denied=%d compressed=%d conversation_id=%s",
                    tool_stats["total_calls"], tool_stats["denied"],
                    tool_stats["compressed"], conv_id,
                )

                confidence_result = self._assess_confidence(messages, system_prompt)

                return {
                    "answer": final_text.strip(),
                    "confidence": confidence_result.get("confidence", "medium"),
                    "confidence_reason": confidence_result.get("confidence_reason", ""),
                    "queries": queries_log,
                    "ruled_out": confidence_result.get("ruled_out", []),
                    "evidence_summary": confidence_result.get("evidence_summary", ""),
                    "conversation_id": conv_id,
                    "plan": plan,
                    "tool_stats": tool_stats,
                    "loaded_schema_groups": self.context_manager.loaded_groups,
                }

            # --- LLM requested tool use ---
            elif resp["stop_reason"] == "tool_use":
                assistant_content = resp["content"]
                # Content blocks are already dicts from LLMAdapter
                messages.append({"role": "assistant", "content": assistant_content})

                # Extract reasoning text
                current_reasoning = []
                for block in assistant_content:
                    if block.get("type") == "text" and block.get("text", "").strip():
                        current_reasoning.append(block["text"].strip())

                # Execute all tool calls (supports parallel)
                tool_blocks = [
                    b for b in assistant_content if b.get("type") == "tool_use"
                ]
                tool_results = self._execute_tool_batch(
                    tool_blocks, tool_stats, queries_log,
                    current_reasoning, system_prompt,
                )

                messages.append({"role": "user", "content": tool_results})

            else:
                log.warning("analysis.unexpected_stop stop_reason=%s",
                            resp.get("stop_reason"))
                break

        # Reached max rounds — force a summary
        return self._force_summarize(
            messages, system_prompt, queries_log, conv_id, plan, tool_stats
        )

    # ============================================================
    # Streaming entry point
    # ============================================================

    def analyze_stream(
        self,
        question: str,
        context: str = "",
        conversation_id: str = None,
        today: str = None,
        playbook: str = None,
        image_blocks: list[dict] = None,
    ) -> Iterator[dict]:
        """Like ``analyze()`` but yields events for real-time CLI streaming.

        Event types:
        - ``{"type": "planning", "plan": plan_dict}``
        - ``{"type": "tool_call", "name": "...", "input": {...}}``
        - ``{"type": "tool_result", "name": "...", "preview": "..."}``
        - ``{"type": "text", "text": "partial text..."}``
        - ``{"type": "done", "result": full_result_dict}``
        """
        conv_id = conversation_id or str(uuid.uuid4())

        if today is None:
            today = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # --- Resolve playbook ---
        playbook_text = ""
        if playbook:
            pb = self.playbook_registry.get(playbook)
            if pb:
                playbook_text = pb.content
        else:
            pb = self.playbook_registry.detect_playbook(question)
            if pb:
                playbook_text = pb.content

        # --- Build system prompt ---
        system_prompt = self.context_manager.build_system_prompt(
            question=question,
            today=today,
            context=context,
            playbook_text=playbook_text,
        )

        # --- Planning ---
        plan = None
        plan_text = ""
        if self.enable_planning:
            plan = self._generate_plan(question, context, system_prompt)
            if plan:
                plan_text = format_plan_for_injection(plan)
                yield {"type": "planning", "plan": plan}

        # --- Initial message ---
        text_content = question
        if plan_text:
            text_content = f"{question}\n\n{plan_text}"

        if image_blocks:
            user_content = list(image_blocks)
            user_content.append({"type": "text", "text": text_content})
        else:
            user_content = text_content

        messages: list[dict] = [{"role": "user", "content": user_content}]
        queries_log: list[dict] = []
        tool_stats = {"total_calls": 0, "denied": 0, "compressed": 0, "errors": 0}

        tools_schema = self.tool_registry.get_claude_tools()

        # --- Main loop ---
        for round_num in range(self.max_rounds + 2):
            # Context compression
            if self.enable_context_compression:
                if self._compressor.should_compress(messages, system_prompt, tools_schema):
                    messages = self._compressor.compress(messages, system_prompt)

            # Streaming LLM call — yield text chunks in real time
            try:
                collected_text_parts: list[str] = []
                tool_call_accumulators: dict[int, dict] = {}
                finish_reason = None

                for chunk in self.llm.stream_message_iter(
                    system=system_prompt,
                    messages=messages,
                    tools=tools_schema,
                    max_tokens=self.llm.max_output_tokens,
                    temperature=0,
                ):
                    ctype = chunk.get("type", "empty")

                    if ctype == "text":
                        text_piece = chunk["text"]
                        collected_text_parts.append(text_piece)
                        yield {"type": "text", "text": text_piece}

                    elif ctype == "tool_call_delta":
                        idx = chunk.get("index", 0)
                        if idx not in tool_call_accumulators:
                            tool_call_accumulators[idx] = {
                                "id": chunk.get("id") or "",
                                "name": "",
                                "arguments": "",
                            }
                        acc = tool_call_accumulators[idx]
                        if chunk.get("id"):
                            acc["id"] = chunk["id"]
                        if chunk.get("name"):
                            acc["name"] = chunk["name"]
                        if chunk.get("arguments"):
                            acc["arguments"] += chunk["arguments"]

                    elif ctype == "finish":
                        finish_reason = chunk.get("reason")

            except Exception as e:
                log.error("stream.error error=%s", str(e))
                result = self._error_result(
                    f"API call failed: {e}", conv_id, queries_log
                )
                yield {"type": "done", "result": result}
                return

            # --- Reconstruct a normalized response from accumulated chunks ---
            content_blocks: list[dict] = []
            stop_reason = "end_turn"
            full_text = "".join(collected_text_parts)
            if full_text:
                content_blocks.append({"type": "text", "text": full_text})
            if tool_call_accumulators:
                stop_reason = "tool_use"
                for tc in sorted(
                    tool_call_accumulators.values(), key=lambda x: x.get("id", "")
                ):
                    args = tc["arguments"]
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        args = {"raw": args}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": args,
                    })
            elif finish_reason == "stop":
                stop_reason = "end_turn"

            resp = {"content": content_blocks, "stop_reason": stop_reason}

            # --- Handle response ---
            if resp["stop_reason"] == "end_turn":
                final_text = self._extract_text(resp["content"])
                confidence_result = self._assess_confidence(messages, system_prompt)

                result = {
                    "answer": final_text.strip(),
                    "confidence": confidence_result.get("confidence", "medium"),
                    "confidence_reason": confidence_result.get("confidence_reason", ""),
                    "queries": queries_log,
                    "ruled_out": confidence_result.get("ruled_out", []),
                    "evidence_summary": confidence_result.get("evidence_summary", ""),
                    "conversation_id": conv_id,
                    "plan": plan,
                    "tool_stats": tool_stats,
                    "loaded_schema_groups": self.context_manager.loaded_groups,
                }
                yield {"type": "done", "result": result}
                return

            elif resp["stop_reason"] == "tool_use":
                assistant_content = resp["content"]
                messages.append({"role": "assistant", "content": assistant_content})

                current_reasoning = []
                for block in assistant_content:
                    if block.get("type") == "text" and block.get("text", "").strip():
                        current_reasoning.append(block["text"].strip())

                tool_blocks = [
                    b for b in assistant_content if b.get("type") == "tool_use"
                ]

                # Yield tool_call events
                for tb in tool_blocks:
                    yield {
                        "type": "tool_call",
                        "name": tb["name"],
                        "input": tb["input"],
                    }

                # Execute tools
                tool_results = self._execute_tool_batch(
                    tool_blocks, tool_stats, queries_log,
                    current_reasoning, system_prompt,
                )

                # Yield tool_result events
                for tr in tool_results:
                    yield {
                        "type": "tool_result",
                        "name": tr.get("tool_name", ""),
                        "preview": tr.get("content", "")[:500],
                    }

                messages.append({"role": "user", "content": tool_results})

            else:
                break

        # Max rounds reached
        forced = self._force_summarize(
            messages, system_prompt, queries_log, conv_id, plan, tool_stats
        )
        yield {"type": "done", "result": forced}

    # ============================================================
    # Tool execution (serial / parallel)
    # ============================================================

    def _should_parallelize(self, tool_blocks: list[dict]) -> bool:
        """Determine whether a batch of tool_use blocks can run in parallel.

        Only parallelizes when all tools are in PARALLEL_SAFE_TOOLS.
        """
        if not self.enable_parallel_tools or len(tool_blocks) <= 1:
            return False
        return all(b["name"] in PARALLEL_SAFE_TOOLS for b in tool_blocks)

    def _execute_tool_batch(
        self,
        tool_blocks: list[dict],
        tool_stats: dict,
        queries_log: list[dict],
        current_reasoning: list[str],
        system_prompt: str,
    ) -> list[dict]:
        """Execute a batch of tool calls, supporting parallel execution.

        Returns a list of tool_result dicts suitable for the messages array.
        """
        if self._should_parallelize(tool_blocks):
            log.info("tools.parallel count=%d tools=%s",
                     len(tool_blocks), [b["name"] for b in tool_blocks])
            return self._execute_parallel(
                tool_blocks, tool_stats, queries_log, current_reasoning,
            )
        else:
            return self._execute_serial(
                tool_blocks, tool_stats, queries_log,
                current_reasoning, system_prompt,
            )

    def _process_tool_result(
        self,
        block: dict,
        tool_result: ToolResult,
        tool_stats: dict,
        queries_log: list[dict],
        current_reasoning: list[str],
        dynamic_schema_content: str | None = None,
    ) -> dict:
        """Process a single tool execution result: update stats, log, build result dict."""
        tool_stats["total_calls"] += 1

        if not tool_result.success:
            if "denied" in tool_result.content.lower():
                tool_stats["denied"] += 1
                log.warning("tool.denied tool_name=%s detail=%s",
                            block["name"], tool_result.content[:100])
            else:
                tool_stats["errors"] += 1
                log.error("tool.error tool_name=%s detail=%s",
                          block["name"], tool_result.content[:100])

        if tool_result.metadata.get("compressed"):
            tool_stats["compressed"] += 1

        tool_result_content = tool_result.content
        if dynamic_schema_content:
            tool_result_content = f"{tool_result.content}\n\n{dynamic_schema_content}"

        reason = block["input"].get("reason", "")
        sql = block["input"].get("sql", "")
        queries_log.append({
            "tool": block["name"],
            "reason": reason,
            "sql": sql,
            "input": block["input"],
            "result_preview": tool_result.preview,
            "result_full": tool_result.content,
            "reasoning": "\n".join(current_reasoning),
            "success": tool_result.success,
            "duration_ms": tool_result.duration_ms,
            "metadata": tool_result.metadata,
        })

        return {
            "type": "tool_result",
            "tool_use_id": block["id"],
            "tool_name": block["name"],
            "content": tool_result_content,
        }

    def _execute_serial(
        self,
        tool_blocks: list[dict],
        tool_stats: dict,
        queries_log: list[dict],
        current_reasoning: list[str],
        system_prompt: str,
    ) -> list[dict]:
        """Execute tools sequentially (default path)."""
        tool_results = []
        for block in tool_blocks:
            reason = block["input"].get("reason", "")
            sql = block["input"].get("sql", "")
            log.info("tool.call tool_num=%d tool_name=%s reason=%s sql_preview=%s",
                     tool_stats["total_calls"] + 1, block["name"], reason,
                     sql[:150] if sql else None)

            tool_result = self.tool_registry.execute(block["name"], block["input"])

            # Runtime schema supplementation (only for run_sql)
            dynamic_schema = None
            if block["name"] == "run_sql" and sql:
                additional = self.context_manager.request_additional_schema(sql)
                if additional:
                    dynamic_schema = additional

            result_dict = self._process_tool_result(
                block, tool_result, tool_stats, queries_log,
                current_reasoning, dynamic_schema,
            )
            tool_results.append(result_dict)
            current_reasoning = []

        return tool_results

    def _execute_parallel(
        self,
        tool_blocks: list[dict],
        tool_stats: dict,
        queries_log: list[dict],
        current_reasoning: list[str],
    ) -> list[dict]:
        """Execute tools in parallel (only for PARALLEL_SAFE_TOOLS)."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        max_workers = min(len(tool_blocks), _MAX_PARALLEL_WORKERS)
        results: list[ToolResult | None] = [None] * len(tool_blocks)

        # Log all calls
        for block in tool_blocks:
            reason = block["input"].get("reason", "")
            log.info("tool.call.parallel tool_name=%s reason=%s",
                     block["name"], reason)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_idx = {
                pool.submit(self.tool_registry.execute, b["name"], b["input"]): i
                for i, b in enumerate(tool_blocks)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    log.error("tool.parallel_error tool_name=%s error=%s",
                              tool_blocks[idx]["name"], str(e))
                    results[idx] = ToolResult(
                        content=f"Parallel execution error: {e}",
                        success=False,
                        tool_name=tool_blocks[idx]["name"],
                        input_args=tool_blocks[idx]["input"],
                    )

        # Process results in order
        tool_results = []
        for i, block in enumerate(tool_blocks):
            result_dict = self._process_tool_result(
                block, results[i], tool_stats, queries_log,
                current_reasoning if i == 0 else [],
            )
            tool_results.append(result_dict)

        return tool_results

    # ============================================================
    # Planning
    # ============================================================

    def _generate_plan(
        self, question: str, context: str, system_prompt: str
    ) -> dict | None:
        """Use the LLM to generate a structured analysis plan."""
        try:
            planning_prompt = build_planning_prompt(question, context)
            resp = self.llm.create_message(
                system=(
                    "You are a data analysis plan generator. "
                    "Given a question, produce a structured analysis plan. "
                    "Output JSON directly with no other text."
                ),
                messages=[{"role": "user", "content": planning_prompt}],
                max_tokens=2048,
                temperature=0,
            )

            text = self._extract_text(resp.get("content", []))
            plan = self._extract_json(text)
            if plan and "steps" in plan:
                return plan

            log.warning("plan.parse_failed output_preview=%s", text[:200])
            return None

        except Exception as e:
            log.warning("plan.generation_failed error=%s", str(e))
            return None

    # ============================================================
    # Confidence assessment
    # ============================================================

    def _assess_confidence(self, messages: list[dict], system_prompt: str) -> dict:
        """Perform a confidence self-assessment using the LLM."""
        default = {
            "confidence": "medium",
            "confidence_reason": "Self-assessment parsing failed",
            "ruled_out": [],
            "evidence_summary": "",
        }
        try:
            # Build a simplified message history for confidence assessment.
            # Strip tool_use/tool_result blocks to avoid LiteLLM errors
            # when calling without tools= param.
            assess_messages = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content")
                if isinstance(content, str):
                    assess_messages.append(msg)
                elif isinstance(content, list):
                    texts = [
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    combined = "\n".join(t for t in texts if t.strip())
                    if combined:
                        assess_messages.append({"role": role, "content": combined})

            assess_messages.append({"role": "user", "content": CONFIDENCE_PROMPT})

            resp = self.llm.create_message(
                system=system_prompt,
                messages=assess_messages,
                max_tokens=1024,
                temperature=0,
            )

            text = self._extract_text(resp.get("content", []))
            result = self._extract_json(text)
            if result and "confidence" in result:
                return result

            log.warning("confidence.parse_failed output_preview=%s", text[:200])
            return default
        except Exception as e:
            log.error("confidence.error error=%s", str(e))
            return default

    # ============================================================
    # Force summarize (max rounds reached)
    # ============================================================

    def _force_summarize(
        self,
        messages: list[dict],
        system_prompt: str,
        queries_log: list[dict],
        conv_id: str,
        plan: dict | None,
        tool_stats: dict,
    ) -> dict:
        """When the max round limit is reached, force the LLM to summarize."""
        log.warning("analysis.max_rounds max_rounds=%d", self.max_rounds)
        messages.append({
            "role": "user",
            "content": (
                "Stop querying. You have reached the maximum round limit. "
                "Based on all the data you have collected so far, produce "
                "a complete final analysis report immediately. "
                "Do not say 'let me continue' -- write the report now."
            ),
        })

        try:
            resp = self.llm.stream_message(
                system=system_prompt,
                messages=messages,
                max_tokens=self.llm.max_output_tokens,
                temperature=0,
            )
            final_text = self._extract_text(resp.get("content", []))
        except Exception:
            final_text = "Analysis timed out. Below is a summary of the data collected so far."

        confidence_result = self._assess_confidence(messages, system_prompt)

        return {
            "answer": final_text.strip(),
            "confidence": confidence_result.get("confidence", "low"),
            "confidence_reason": confidence_result.get(
                "confidence_reason", "Reached maximum query rounds"
            ),
            "queries": queries_log,
            "ruled_out": confidence_result.get("ruled_out", []),
            "evidence_summary": confidence_result.get("evidence_summary", ""),
            "conversation_id": conv_id,
            "plan": plan,
            "tool_stats": tool_stats,
            "loaded_schema_groups": self.context_manager.loaded_groups,
        }

    # ============================================================
    # Error result
    # ============================================================

    def _error_result(
        self, error_msg: str, conv_id: str, queries_log: list[dict]
    ) -> dict:
        """Build a standard error result dict."""
        return {
            "answer": f"An error occurred during analysis: {error_msg}",
            "confidence": "low",
            "confidence_reason": str(error_msg),
            "queries": queries_log,
            "ruled_out": [],
            "evidence_summary": "",
            "conversation_id": conv_id,
            "plan": None,
            "tool_stats": {},
            "loaded_schema_groups": [],
        }

    # ============================================================
    # Helpers
    # ============================================================

    @staticmethod
    def _extract_text(content: list[dict]) -> str:
        """Extract concatenated text from a list of content blocks."""
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts)

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """Robustly extract JSON from LLM output.

        Handles code blocks, surrounding prose, and nested braces.
        """
        text = text.strip()

        # 1. Try code-block extraction
        if "```" in text:
            m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
            if m:
                text = m.group(1).strip()

        # 2. Find the outermost { ... } (handles nesting)
        start = text.find("{")
        if start < 0:
            return None

        depth = 0
        end = -1
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == '\\':
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end < 0:
            return None

        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Determine whether an exception is transient and worth retrying."""
        err_str = str(exc).lower()
        return any(
            kw in err_str
            for kw in ("overloaded", "rate_limit", "429", "529", "503", "capacity", "timeout")
        )
