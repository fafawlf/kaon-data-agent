from __future__ import annotations

"""
Unified Tool base class — inspired by Claude Code's Tool<Input, Output> design.

Every tool must implement:
- name, description, input_schema: tool metadata
- execute(): core execution logic
- validate_input(): input validation (called automatically before execution)

Optional overrides:
- pre_execute(): PreToolUse hook — intercept/modify/deny before execution
- post_execute(): PostToolUse hook — process result after execution
- is_read_only(): mark whether the tool is read-only
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
import time
import traceback


@dataclass
class ToolResult:
    """Unified wrapper for tool execution results."""
    success: bool
    content: str
    tool_name: str
    input_args: dict
    duration_ms: float = 0
    metadata: dict = field(default_factory=dict)  # Extra info (row count, truncation flag, etc.)
    error: str = ""

    @property
    def preview(self) -> str:
        return self.content[:500]


class ValidationError(Exception):
    """Input validation failure."""
    pass


class ToolDeniedError(Exception):
    """PreToolUse hook denied execution."""
    pass


class BaseTool(ABC):
    """Base class for all tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name, corresponds to the name field in Claude tool_use."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description, used in Claude system prompt."""
        ...

    @property
    @abstractmethod
    def input_schema(self) -> dict:
        """Input definition in JSON Schema format."""
        ...

    def is_read_only(self) -> bool:
        """Whether this tool is read-only. Defaults to True; write tools should override."""
        return True

    def validate_input(self, tool_input: dict) -> dict:
        """
        Input validation. Returns (possibly modified) input, or raises ValidationError.
        Default implementation: checks required fields. Subclasses can override for
        more complex validation.
        """
        required = self.input_schema.get("required", [])
        for field_name in required:
            if field_name not in tool_input or tool_input[field_name] is None:
                raise ValidationError(f"Missing required parameter: {field_name}")
        return tool_input

    def pre_execute(self, tool_input: dict) -> dict:
        """
        PreToolUse Hook — called before execute.
        Can modify input, log, or raise ToolDeniedError to block execution.
        Returns (possibly modified) input.
        """
        return tool_input

    def post_execute(self, result: ToolResult) -> ToolResult:
        """
        PostToolUse Hook — called after execute.
        Can modify result, compress output, add metadata.
        """
        return result

    @abstractmethod
    def execute(self, tool_input: dict) -> str:
        """Core execution logic. Returns result text."""
        ...

    def run(self, tool_input: dict) -> ToolResult:
        """
        Complete execution flow: validate -> pre_execute -> execute -> post_execute
        This is the only external entry point.
        """
        start = time.time()

        try:
            # 1. Input validation
            validated_input = self.validate_input(tool_input)

            # 2. PreToolUse hook
            final_input = self.pre_execute(validated_input)

            # 3. Core execution
            content = self.execute(final_input)

            duration = (time.time() - start) * 1000
            result = ToolResult(
                success=True,
                content=content,
                tool_name=self.name,
                input_args=final_input,
                duration_ms=duration,
            )

            # 4. PostToolUse hook
            result = self.post_execute(result)
            return result

        except ValidationError as e:
            return ToolResult(
                success=False,
                content=f"Input validation failed: {str(e)}",
                tool_name=self.name,
                input_args=tool_input,
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
            )
        except ToolDeniedError as e:
            return ToolResult(
                success=False,
                content=f"Tool execution denied: {str(e)}",
                tool_name=self.name,
                input_args=tool_input,
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
            )
        except Exception as e:
            return ToolResult(
                success=False,
                content=f"Execution error: {str(e)}",
                tool_name=self.name,
                input_args=tool_input,
                duration_ms=(time.time() - start) * 1000,
                error=traceback.format_exc(),
            )

    def to_claude_schema(self) -> dict:
        """Generate the tool definition format required by the Claude API."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    """Tool registry — manages all available tools."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def execute(self, tool_name: str, tool_input: dict) -> ToolResult:
        """Unified execution entry point."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult(
                success=False,
                content=f"Unknown tool: {tool_name}",
                tool_name=tool_name,
                input_args=tool_input,
                error=f"Tool '{tool_name}' not registered",
            )
        return tool.run(tool_input)

    def get_claude_tools(self) -> list[dict]:
        """Generate Claude API schema list for all tools."""
        return [t.to_claude_schema() for t in self._tools.values()]

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())
