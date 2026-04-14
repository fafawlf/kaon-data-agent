<div align="center">

# Kaon Data Agent (KDA)

**The first open-source playbook-driven data analysis agent.**

KDA doesn't just translate questions to SQL — it investigates root causes,<br>
decomposes by dimensions, cross-validates, and delivers actionable conclusions.

[![PyPI](https://img.shields.io/pypi/v/kaon-data-agent?color=blue)](https://pypi.org/project/kaon-data-agent/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![GitHub stars](https://img.shields.io/github/stars/fafawlf/kaon-data-agent?style=social)](https://github.com/fafawlf/kaon-data-agent)

[Quick Start](#-quick-start) | [How It Works](#-how-it-works) | [Docs](#-configuration) | [Community](#-community)

</div>

---

**[View a sample analysis report](https://htmlpreview.github.io/?https://github.com/fafawlf/kaon-data-agent/blob/main/docs/assets/demo_report.html)** — KDA investigated a DAU drop, ran 11 SQL queries across 9 rounds, identified India + Japan as the root cause (91%+ drop, 100% contribution), and produced this report automatically.

## Why KDA?

Most "AI + SQL" tools translate a natural-language question into a single query. That solves *"what is X?"* but not *"why did X change?"* or *"what should we do?"*

| Capability | Vanna / PandasAI / WrenAI | KDA |
|---|---|---|
| Single question to SQL | Yes | Yes |
| Multi-step investigation (5-15 queries) | No | Yes |
| Dimension decomposition + contribution % | No | Yes |
| Root-cause diagnosis | No | Yes (playbook) |
| AB experiment analysis (z-test, Bonferroni) | No | Yes (playbook) |
| Quantified conclusions + recommendations | Partial | Yes (enforced) |
| Knowledge-base integration | Partial | Yes |
| Context-window management | N/A | Yes (auto-compression) |

**KDA operates at a different level**: it answers *why* and *what to do*, not just *what*.

---

## Features

- **Playbook-Driven Investigation** — Three built-in analysis frameworks (anomaly diagnosis, AB experiment, exploratory) that guide the agent through structured analytical reasoning, not just query generation.
- **Multi-Round Agentic Analysis** — The agent plans hypotheses, writes SQL, interprets results, adjusts direction, and iterates — typically 5–15 queries per investigation.
- **Evidence-Chain Reports** — Every conclusion is backed by specific numbers, contribution percentages, and cross-validated signals. The output is a narrative, not a table dump.
- **Knowledge Base Integration** — Drop `.md` files describing your metrics, tables, and business context. The agent loads them on demand to write correct SQL.
- **Context Window Management** — Automatic compression of conversation history and large query results so the agent can sustain 20+ round investigations.
- **Custom Playbooks** — Write your own `.md` playbooks with YAML frontmatter. Define triggers, steps, and disciplines for any recurring analysis pattern.

---

## Quick Start

### 30-second demo

```bash
pip install kaon-data-agent
export ANTHROPIC_API_KEY=sk-ant-...
kda demo
```

This creates a demo SQLite database (SaaS product with a deliberate DAU anomaly) and starts an interactive session. Try asking:

```
> What caused the DAU drop about 30 days ago?
> Analyze the new_onboarding_flow experiment
> What are the top countries by revenue per user?
```

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/fafawlf/kaon-data-agent/blob/main/examples/demo/demo.ipynb)

### Connect your own database

```bash
pip install kaon-data-agent

# 1. Write a config file
cat > config.yaml << 'EOF'
llm:
  model: claude-sonnet-4-20250514
database:
  connection_string: postgresql://user:pass@localhost/mydb
knowledge:
  directory: ./knowledge
context:
  role: >
    You are a data analyst for an e-commerce platform.
    Focus on conversion, retention, and revenue metrics.
  sql_dialect: postgresql
EOF

# 2. Add a few knowledge files
mkdir knowledge
cat > knowledge/metrics.md << 'EOF'
# Core Metrics
- **DAU**: COUNT(DISTINCT user_id) from sessions WHERE date = ...
- **Revenue**: SUM(amount) from orders
- **D1 Retention**: Users active on day 1 / Users signed up on day 0
EOF

# 3. Generate the knowledge index
kda index ./knowledge

# 4. Launch
export ANTHROPIC_API_KEY=sk-ant-...
kda --config config.yaml
```

---

## How It Works

<div align="center">

```
  User Question ──────► Planner ──────► Playbook Router
                        (hypotheses)    (diagnostic / AB / exploratory)
                                              │
                        ┌─────────────────────▼────────────────────┐
                        │           Agentic Loop (5-15 rounds)     │
                        │                                          │
                        │  LLM ─── reason ──► Tools ──► interpret  │
                        │   ▲                   │                   │
                        │   └───── iterate ◄────┘                   │
                        │                                          │
                        │  Tools: run_sql · discover_tables ·      │
                        │         get_table_schema ·                │
                        │         search_knowledge_base             │
                        └─────────────────────┬────────────────────┘
                                              │
                        Context Manager ◄─────┤  (dynamic schema, compression)
                                              │
                        Evidence-Chain Report ◄┘  (what → why → what to do)
```

</div>

### Three-layer analysis

| Layer | What it does | Example |
|-------|-------------|---------|
| **Statistical** | Confirm anomaly, quantify deviation, check significance | "DAU dropped 22% on Mar 14, 2.4σ below baseline" |
| **Dimensional** | Decompose by segments, calculate contribution % | "India contributed 64%, Japan 39% of the drop" |
| **Causal** | Investigate mechanism, cross-validate, build narrative | "Regional outage in APAC — all platforms affected, recovery on Mar 17" |

---

## Supported Databases

KDA uses SQLAlchemy — any database with a dialect works.

| Database | Install | Status |
|----------|---------|--------|
| SQLite | built-in | Supported |
| PostgreSQL | `pip install kaon-data-agent[postgresql]` | Supported |
| MySQL / StarRocks | `pip install kaon-data-agent[mysql]` | Supported |
| DuckDB | `pip install kaon-data-agent[duckdb]` | Supported |
| BigQuery | — | Planned |
| Snowflake | — | Planned |
| ClickHouse | — | Planned |

---

## Built-in Playbooks

### Metric Anomaly Diagnosis
> *Triggers: anomaly, drop, spike, decline, "why did", "what happened"*

Six-step framework: confirm anomaly → dimension decomposition → volume vs. rate distinction → root cause → cross-validation → recommendations. Enforces contribution percentages and evidence chains.

### AB Experiment Analysis
> *Triggers: AB test, experiment, treatment, control, variant*

Seven-step framework: SRM check → baseline validation → causal hypothesis → metrics with z-test & Bonferroni → dimension drill-down → behavioral verification → conclusion. Uses `{{experiment_table}}` placeholders — bring your own schema.

### Exploratory Analysis
> *Triggers: explore, investigate, "tell me about", overview, deep dive*

Lightweight framework: understand question → data discovery → iterative analysis → synthesis. Open-ended but focused.

---

## Configuration

### `config.yaml` reference

```yaml
llm:
  provider: anthropic          # anthropic, openai, deepseek (via LiteLLM)
  model: claude-sonnet-4-20250514
  temperature: 0               # deterministic by default
  max_output_tokens: 16384

database:
  connection_string: sqlite:///data.sqlite  # any SQLAlchemy URL

agent:
  max_rounds: 20               # max LLM turns per analysis
  enable_planning: true         # hypothesis-driven plan before execution
  enable_compression: true      # compress large tool results
  enable_context_compression: true  # summarize long conversations

knowledge:
  directory: ./knowledge        # .md files with domain knowledge

playbooks:
  directory: ""                 # custom playbooks (empty = built-in only)

context:
  role: "You are a data analyst for ..."
  sql_dialect: sqlite           # sqlite, postgresql, mysql, starrocks
  dimensions:                   # common drill-down dimensions
    - country
    - platform
    - user_segment
```

### Knowledge files

Write plain Markdown files in your knowledge directory. Each file covers one domain: metric definitions, table documentation, business context, SQL gotchas.

```bash
kda index ./knowledge   # generate index.json after edits
```

The agent sees the index at session start and pulls in full documents on demand via `search_knowledge_base`.

### Custom playbooks

```markdown
---
name: funnel_analysis
description: Conversion funnel investigation
triggers: [funnel, conversion, drop-off, onboarding]
---

## Step 1: Define the funnel stages
Query each stage's user count for the target period...

## Step 2: Calculate stage-to-stage conversion
...
```

---

## CLI Commands

```bash
kda --config config.yaml       # Interactive REPL
kda demo                        # Bundled demo database
kda index <directory>           # Generate knowledge index.json
kda --config config.yaml check  # Validate config, DB, LLM, knowledge
```

---

## Roadmap

- [ ] Web UI (Chainlit / Streamlit)
- [ ] Demo GIF and Colab notebook
- [ ] BigQuery, Snowflake, ClickHouse adapters
- [ ] MCP server integration for enterprise metadata
- [ ] Chart generation and visual diagnostics
- [ ] Community playbook library
- [ ] Scheduled analysis and alerting
- [ ] Chinese README

---

## Community

<!-- TODO: Add links once created -->
<!-- - [Discord](https://discord.gg/xxx) -->
- [GitHub Issues](https://github.com/fafawlf/kaon-data-agent/issues) — Bug reports and feature requests
- [GitHub Discussions](https://github.com/fafawlf/kaon-data-agent/discussions) — Questions and ideas

If KDA saved you time, consider giving it a star — it helps other analysts find this tool.

### Contributors

Created by **[Lifan Wang](https://kaon.io)** — contributions welcome!

[![Contributors](https://contrib.rocks/image?repo=fafawlf/kaon-data-agent)](https://github.com/fafawlf/kaon-data-agent/graphs/contributors)

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
