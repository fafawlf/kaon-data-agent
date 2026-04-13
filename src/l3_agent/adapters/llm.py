"""LLM adapter — unified interface via LiteLLM, with streaming + retry."""
from __future__ import annotations

import time
import logging
from typing import Any, Iterator

import litellm

log = logging.getLogger("l3_agent.llm")

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True


class LLMAdapter:
    """Unified LLM interface wrapping LiteLLM."""

    def __init__(self, provider: str = "anthropic", model: str = "claude-sonnet-4-20250514",
                 api_key: str = "", temperature: float = 0,
                 max_output_tokens: int = 16384):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

        if api_key:
            self._set_api_key(provider, api_key)

    @staticmethod
    def _set_api_key(provider: str, api_key: str):
        import os
        env_map = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
        }
        env_var = env_map.get(provider, f"{provider.upper()}_API_KEY")
        os.environ[env_var] = api_key

    def _litellm_model(self) -> str:
        # LiteLLM expects provider-prefixed model names for non-OpenAI
        if self.provider == "openai" or "/" in self.model:
            return self.model
        return f"{self.provider}/{self.model}"

    def create_message(self, system: str, messages: list[dict],
                       tools: list[dict] | None = None,
                       max_tokens: int | None = None,
                       temperature: float | None = None) -> dict:
        """Create a non-streaming message. Returns a normalized response dict."""
        kwargs = self._build_kwargs(system, messages, tools, max_tokens, temperature)
        resp = self._call_with_retry(litellm.completion, **kwargs)
        return self._normalize_response(resp)

    def stream_message(self, system: str, messages: list[dict],
                       tools: list[dict] | None = None,
                       max_tokens: int | None = None,
                       temperature: float | None = None) -> dict:
        """Streaming call — collects full response. Returns normalized response."""
        kwargs = self._build_kwargs(system, messages, tools, max_tokens, temperature)
        kwargs["stream"] = True
        chunks = self._call_with_retry(litellm.completion, **kwargs)
        return self._collect_stream(chunks)

    def stream_message_iter(self, system: str, messages: list[dict],
                            tools: list[dict] | None = None,
                            max_tokens: int | None = None,
                            temperature: float | None = None) -> Iterator[dict]:
        """Streaming call — yields chunks for real-time display."""
        kwargs = self._build_kwargs(system, messages, tools, max_tokens, temperature)
        kwargs["stream"] = True
        chunks = self._call_with_retry(litellm.completion, **kwargs)
        for chunk in chunks:
            yield self._normalize_chunk(chunk)

    def _build_kwargs(self, system, messages, tools, max_tokens, temperature):
        kwargs: dict[str, Any] = {
            "model": self._litellm_model(),
            "messages": self._prepare_messages(system, messages),
            "max_tokens": max_tokens or self.max_output_tokens,
            "temperature": temperature if temperature is not None else self.temperature,
        }
        if tools:
            kwargs["tools"] = self._prepare_tools(tools)
        return kwargs

    def _prepare_messages(self, system: str, messages: list[dict]) -> list[dict]:
        """Convert messages to OpenAI format that LiteLLM expects.

        Key conversions:
        - Anthropic tool_use blocks in assistant → OpenAI tool_calls
        - Anthropic tool_result in user → OpenAI role:"tool" messages
        """
        result = []
        if system:
            result.append({"role": "system", "content": system})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content")

            # Simple string content — pass through
            if isinstance(content, str):
                result.append({"role": role, "content": content})
                continue

            if not isinstance(content, list):
                result.append(msg)
                continue

            # Assistant message with tool_use blocks → convert to OpenAI format
            if role == "assistant":
                text_parts = []
                tool_calls = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        import json
                        args = block.get("input", {})
                        tool_calls.append({
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                            },
                        })
                assistant_msg: dict[str, Any] = {"role": "assistant"}
                if text_parts:
                    assistant_msg["content"] = "\n".join(text_parts)
                else:
                    assistant_msg["content"] = None
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                result.append(assistant_msg)
                continue

            # User message with tool_result blocks → convert to role:"tool"
            if role == "user":
                has_tool_results = any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content
                )
                if has_tool_results:
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_result":
                            tool_content = block.get("content", "")
                            if isinstance(tool_content, list):
                                # Extract text from content blocks
                                tool_content = "\n".join(
                                    b.get("text", str(b))
                                    for b in tool_content
                                    if isinstance(b, dict)
                                )
                            result.append({
                                "role": "tool",
                                "tool_call_id": block.get("tool_use_id", ""),
                                "content": str(tool_content),
                            })
                        elif block.get("type") == "text":
                            result.append({"role": "user", "content": block.get("text", "")})
                else:
                    # Regular user content blocks
                    text = "\n".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                    if text:
                        result.append({"role": "user", "content": text})
                    else:
                        result.append(msg)
                continue

            result.append(msg)

        return result

    @staticmethod
    def _prepare_tools(tools: list[dict]) -> list[dict]:
        """Convert Anthropic-style tool schemas to OpenAI function-calling format."""
        result = []
        for tool in tools:
            result.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            })
        return result

    def _call_with_retry(self, fn, **kwargs):
        max_retries = 5
        base_delay = 10
        for attempt in range(max_retries + 1):
            try:
                return fn(**kwargs)
            except Exception as e:
                if not self._is_retryable(e) or attempt == max_retries:
                    raise
                delay = base_delay * (2 ** attempt)
                log.warning("LLM retry attempt=%d delay=%ds error=%s",
                            attempt + 1, delay, str(e)[:120])
                time.sleep(delay)

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        err_str = str(exc).lower()
        return any(kw in err_str for kw in
                   ("overloaded", "rate_limit", "429", "529", "503", "capacity", "timeout"))

    def _normalize_response(self, resp) -> dict:
        """Normalize LiteLLM response to a common format."""
        choice = resp.choices[0]
        message = choice.message

        content_blocks = []
        stop_reason = "end_turn"

        if message.content:
            content_blocks.append({"type": "text", "text": message.content})

        if message.tool_calls:
            stop_reason = "tool_use"
            for tc in message.tool_calls:
                import json
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": args,
                })
        elif choice.finish_reason == "stop":
            stop_reason = "end_turn"

        return {
            "content": content_blocks,
            "stop_reason": stop_reason,
            "usage": {
                "input_tokens": getattr(resp.usage, "prompt_tokens", 0),
                "output_tokens": getattr(resp.usage, "completion_tokens", 0),
            },
        }

    def _collect_stream(self, chunks) -> dict:
        """Collect streaming chunks into a single normalized response."""
        text_parts = []
        tool_calls: dict[int, dict] = {}
        finish_reason = None

        for chunk in chunks:
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue
            delta = choice.delta
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            if delta and delta.content:
                text_parts.append(delta.content)
            if delta and delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls:
                        tool_calls[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                    if tc.id:
                        tool_calls[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls[idx]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_calls[idx]["arguments"] += tc.function.arguments

        content_blocks = []
        stop_reason = "end_turn"
        full_text = "".join(text_parts)
        if full_text:
            content_blocks.append({"type": "text", "text": full_text})
        if tool_calls:
            stop_reason = "tool_use"
            import json
            for tc in sorted(tool_calls.values(), key=lambda x: x.get("id", "")):
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

        return {"content": content_blocks, "stop_reason": stop_reason}

    @staticmethod
    def _normalize_chunk(chunk) -> dict:
        """Normalize a single streaming chunk."""
        choice = chunk.choices[0] if chunk.choices else None
        if not choice:
            return {"type": "empty"}
        delta = choice.delta
        if delta and delta.content:
            return {"type": "text", "text": delta.content}
        if delta and delta.tool_calls:
            tc = delta.tool_calls[0]
            return {
                "type": "tool_call_delta",
                "index": tc.index,
                "id": tc.id,
                "name": getattr(tc.function, "name", None),
                "arguments": getattr(tc.function, "arguments", None),
            }
        if choice.finish_reason:
            return {"type": "finish", "reason": choice.finish_reason}
        return {"type": "empty"}
