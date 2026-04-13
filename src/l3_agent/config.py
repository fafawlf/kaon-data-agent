"""Configuration system — file-driven setup with Pydantic validation."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-20250514"
    api_key: Optional[str] = None
    temperature: float = 0
    max_output_tokens: int = 16384

    def resolve_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        env_map = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
        }
        env_var = env_map.get(self.provider, f"{self.provider.upper()}_API_KEY")
        return os.environ.get(env_var, "")


class DatabaseConfig(BaseModel):
    connection_string: str = "sqlite:///demo.sqlite"


class MCPConfig(BaseModel):
    url: str = ""
    headers: dict = Field(default_factory=dict)


class AgentConfig(BaseModel):
    max_rounds: int = 20
    enable_planning: bool = True
    enable_compression: bool = True
    enable_context_compression: bool = True
    enable_parallel_tools: bool = True


class KnowledgeConfig(BaseModel):
    directory: str = "./knowledge"


class PlaybookConfig(BaseModel):
    directory: str = ""  # empty = use built-in playbooks


class SchemaGroup(BaseModel):
    display: str
    tables: list[str]
    triggers: list[str]


class ContextConfig(BaseModel):
    role: str = (
        "You are an autonomous data analyst. You don't just query data — "
        "you investigate root causes, decompose by dimensions, and provide "
        "actionable conclusions backed by evidence chains."
    )
    analysis_principles: str = ""
    table_rules: str = ""
    sql_dialect: str = "sqlite"
    schema_groups: dict[str, SchemaGroup] = Field(default_factory=dict)
    dimensions: list[str] = Field(
        default_factory=lambda: ["country", "platform", "user_segment"]
    )


class L3Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    playbooks: PlaybookConfig = Field(default_factory=PlaybookConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)


def _resolve_env_vars(value):
    """Recursively resolve ${ENV_VAR} references in config values."""
    if isinstance(value, str) and "${" in value:
        import re
        def _replace(m):
            return os.environ.get(m.group(1), "")
        return re.sub(r'\$\{(\w+)\}', _replace, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


def load_config(path: str | Path) -> L3Config:
    """Load config from a YAML file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        if yaml is not None:
            raw = yaml.safe_load(f) or {}
        else:
            import json
            raw = json.load(f)  # fallback: accept JSON config if no PyYAML

    raw = _resolve_env_vars(raw)
    return L3Config(**raw)
