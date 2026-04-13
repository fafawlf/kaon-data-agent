"""L3 Agent CLI — interactive data analysis in your terminal."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.status import Status
from rich.text import Text
from rich.theme import Theme

# ---------------------------------------------------------------------------
# Theme & console
# ---------------------------------------------------------------------------

L3_THEME = Theme({
    "tool.name": "bold cyan",
    "tool.sql": "dim white",
    "tool.result": "green",
    "tool.error": "bold red",
    "plan.title": "bold yellow",
    "plan.step": "dim yellow",
    "confidence": "bold magenta",
    "header": "bold blue",
})

console = Console(theme=L3_THEME)

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _print_welcome(config_path: str, model: str, db: str):
    """Print the welcome banner with config summary."""
    console.print()
    console.print(
        Panel(
            Text.from_markup(
                f"[bold blue]L3 Data Agent[/bold blue]\n"
                f"Database: [dim]{db}[/dim] | "
                f"Model: [dim]{model}[/dim]"
            ),
            border_style="blue",
            padding=(0, 2),
        )
    )
    console.print(
        f"  Config: [dim]{config_path}[/dim]  |  "
        f"Type [bold]exit[/bold] or press Ctrl+C to quit.\n"
    )


def _display_plan(plan: dict):
    """Render an analysis plan."""
    title = plan.get("plan_title", "Analysis Plan")
    console.print(f"\n[plan.title][Planning][/plan.title] {title}")
    hypotheses = plan.get("hypothesis", [])
    for h in hypotheses:
        console.print(f"  [dim]H:[/dim] {h}")
    est = plan.get("estimated_queries", "?")
    console.print(f"  [dim]Estimated queries: {est}[/dim]\n")


def _display_tool_call(tool_name: str, tool_input: dict):
    """Show a tool call with its parameters."""
    console.print(f"[tool.name][Tool][/tool.name] {tool_name}")
    if tool_name == "run_sql" and "sql" in tool_input:
        sql_preview = tool_input["sql"].strip()
        if len(sql_preview) > 200:
            sql_preview = sql_preview[:200] + "..."
        console.print(f"  [tool.sql]{sql_preview}[/tool.sql]")
    elif tool_name == "search_knowledge_base":
        domain = tool_input.get("domain", "")
        keyword = tool_input.get("keyword", "")
        if domain:
            console.print(f"  [dim]domain={domain}[/dim]")
        if keyword:
            console.print(f"  [dim]keyword={keyword}[/dim]")
    elif "keyword" in tool_input:
        console.print(f"  [dim]keyword={tool_input['keyword']}[/dim]")
    elif "table" in tool_input:
        console.print(f"  [dim]table={tool_input['table']}[/dim]")


def _display_tool_result(result: dict):
    """Show tool result summary."""
    if result.get("success", True):
        content = result.get("content", "")
        # Count rows if it looks like a table result
        lines = content.strip().split("\n")
        data_lines = [l for l in lines if l.startswith("|")]
        if len(data_lines) > 2:
            row_count = len(data_lines) - 2  # subtract header and separator
            duration = result.get("duration_ms", 0)
            console.print(
                f"  [tool.result]-> {row_count} rows ({duration:.0f}ms)[/tool.result]"
            )
        else:
            preview = content[:120].replace("\n", " ")
            console.print(f"  [tool.result]-> {preview}[/tool.result]")
    else:
        error = result.get("error", result.get("content", "unknown error"))
        console.print(f"  [tool.error]Error: {error}[/tool.error]")


def _display_streaming_text(text: str):
    """Render the final assistant text as rich Markdown."""
    console.print()
    console.print(Markdown(text))
    console.print()


def _display_footer(
    confidence: str | None,
    query_count: int,
    duration_secs: float,
):
    """Show the analysis summary footer."""
    parts = []
    if confidence:
        parts.append(f"[confidence]Confidence: {confidence}[/confidence]")
    parts.append(f"Queries: {query_count}")
    parts.append(f"Duration: {duration_secs:.1f}s")
    console.print(" | ".join(parts))
    console.print()


# ---------------------------------------------------------------------------
# Agent execution wrapper
# ---------------------------------------------------------------------------

def _run_analysis(agent, question: str, messages: list[dict]) -> list[dict]:
    """Run a single analysis turn and display results.

    Tries streaming first, falls back to non-streaming.
    Returns the updated message history.
    """
    start = time.time()
    query_count = 0
    confidence = None
    streaming_text = False  # Track whether we're mid-stream for text

    try:
        # Try streaming if available
        if hasattr(agent, "analyze_stream"):
            for event in agent.analyze_stream(question):
                etype = event.get("type", "")
                if etype == "planning":
                    _display_plan(event.get("plan", {}))
                elif etype == "tool_call":
                    # End any in-progress text stream before tool output
                    if streaming_text:
                        console.print()
                        streaming_text = False
                    _display_tool_call(
                        event.get("name", ""),
                        event.get("input", {}),
                    )
                elif etype == "tool_result":
                    _display_tool_result({
                        "success": True,
                        "content": event.get("preview", ""),
                        "tool_name": event.get("name", ""),
                    })
                    if event.get("name") == "run_sql":
                        query_count += 1
                elif etype == "text":
                    # Stream text chunks in real time
                    sys.stdout.write(event.get("text", ""))
                    sys.stdout.flush()
                    streaming_text = True
                elif etype == "done":
                    # End any in-progress text stream
                    if streaming_text:
                        console.print()
                        streaming_text = False
                    result = event.get("result", {})
                    confidence = result.get("confidence")
                    # Display the final answer as formatted Markdown
                    answer = result.get("answer", "")
                    if answer:
                        _display_streaming_text(answer)
        else:
            # Fallback: non-streaming analyze()
            with console.status("[bold]Analyzing...", spinner="dots"):
                result = agent.analyze(question)

            # Display plan if present
            if result.get("plan"):
                _display_plan(result["plan"])

            # Display final text
            answer = result.get("answer", "")
            if answer:
                _display_streaming_text(answer)

            confidence = result.get("confidence")
            query_count = result.get("tool_stats", {}).get("total_calls", 0)

    except KeyboardInterrupt:
        if streaming_text:
            console.print()
        console.print("\n[dim]Analysis interrupted.[/dim]")
        return messages

    duration = time.time() - start
    _display_footer(confidence, query_count, duration)
    return messages


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_demo(args):
    """Run the interactive demo with the bundled SQLite database."""
    demo_dir = Path(__file__).resolve().parent.parent.parent / "examples" / "demo"
    db_path = demo_dir / "demo.sqlite"
    config_path = demo_dir / "config.yaml"

    if not db_path.exists():
        console.print("[bold]Demo database not found. Seeding now...[/bold]\n")
        import runpy
        seed_script = demo_dir / "seed_demo_db.py"
        if seed_script.exists():
            runpy.run_path(str(seed_script), run_name="__main__")
        else:
            console.print(f"[red]Seed script not found: {seed_script}[/red]")
            sys.exit(1)
        console.print()

    if not config_path.exists():
        console.print(f"[red]Demo config not found: {config_path}[/red]")
        sys.exit(1)

    # Override args for the REPL
    args.config = str(config_path)
    args.model = getattr(args, "model", None)
    cmd_repl(args)


def cmd_index(args):
    """Scan a directory of .md files and generate index.json."""
    target_dir = Path(args.directory).resolve()
    if not target_dir.is_dir():
        console.print(f"[red]Not a directory: {target_dir}[/red]")
        sys.exit(1)

    entries = []
    md_files = sorted(target_dir.glob("*.md"))

    for md_file in md_files:
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        # Extract title from first # heading
        title = md_file.stem
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("# "):
                title = line[2:].strip()
                break

        # First 200 chars as annotation (skip title line)
        body_start = text.find("\n") + 1
        annotation = text[body_start:body_start + 200].strip().replace("\n", " ")

        entries.append({
            "domain": md_file.stem,
            "path": md_file.name,
            "annotation": f"{title}: {annotation}" if annotation else title,
        })

    output_path = target_dir / "index.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)

    console.print(f"Indexed {len(entries)} file(s) -> {output_path}")
    for e in entries:
        console.print(f"  - [bold]{e['domain']}[/bold]: {e['annotation'][:80]}...")


def cmd_check(args):
    """Validate configuration: database, LLM, knowledge directory."""
    from l3_agent.config import load_config

    config_path = args.config
    if not config_path:
        console.print("[red]--config is required for check[/red]")
        sys.exit(1)

    console.print(f"[bold]Checking configuration: {config_path}[/bold]\n")
    passed = 0
    failed = 0

    # 1. Load config
    try:
        config = load_config(config_path)
        console.print("[green]  Config file ... OK[/green]")
        passed += 1
    except Exception as e:
        console.print(f"[red]  Config file ... FAIL: {e}[/red]")
        failed += 1
        return

    # 2. Database connection
    try:
        from l3_agent.adapters.database import SQLAlchemyAdapter
        db = SQLAlchemyAdapter(config.database.connection_string)
        tables = db.get_tables()
        console.print(f"[green]  Database ..... OK ({len(tables)} tables)[/green]")
        passed += 1
    except Exception as e:
        console.print(f"[red]  Database ..... FAIL: {e}[/red]")
        failed += 1

    # 3. LLM API key
    try:
        api_key = config.llm.resolve_api_key()
        if api_key:
            console.print(
                f"[green]  LLM API key .. OK "
                f"({config.llm.provider}/{config.llm.model})[/green]"
            )
            passed += 1
        else:
            console.print(
                f"[yellow]  LLM API key .. WARN: no key found for "
                f"{config.llm.provider}[/yellow]"
            )
            failed += 1
    except Exception as e:
        console.print(f"[red]  LLM API key .. FAIL: {e}[/red]")
        failed += 1

    # 4. Knowledge directory
    knowledge_dir = Path(config.knowledge.directory)
    if knowledge_dir.is_dir():
        md_count = len(list(knowledge_dir.glob("*.md")))
        has_index = (knowledge_dir / "index.json").exists()
        idx_status = "indexed" if has_index else "no index.json"
        console.print(
            f"[green]  Knowledge .... OK "
            f"({md_count} .md files, {idx_status})[/green]"
        )
        passed += 1
    else:
        console.print(
            f"[yellow]  Knowledge .... WARN: directory not found: "
            f"{knowledge_dir}[/yellow]"
        )
        failed += 1

    # Summary
    console.print()
    if failed == 0:
        console.print(f"[bold green]All {passed} checks passed.[/bold green]")
    else:
        console.print(
            f"[bold yellow]{passed} passed, {failed} failed/warning.[/bold yellow]"
        )


def cmd_repl(args):
    """Interactive REPL: the main experience."""
    from l3_agent.config import load_config
    from l3_agent.adapters.database import SQLAlchemyAdapter
    from l3_agent.adapters.llm import LLMAdapter
    from l3_agent.tools.builtin import create_default_tools
    from l3_agent.context.manager import ContextManager
    from l3_agent.playbooks.base import create_default_playbook_registry

    # Load config
    config = load_config(args.config)
    if args.model:
        config.llm.model = args.model

    # Resolve relative sqlite:/// paths relative to the config file's directory
    conn_str = config.database.connection_string
    if conn_str.startswith("sqlite:///") and not conn_str.startswith("sqlite:////"):
        # Relative path — resolve against config file location
        config_dir = Path(args.config).resolve().parent
        rel_db_path = conn_str[len("sqlite:///"):]
        abs_db_path = (config_dir / rel_db_path).resolve()
        config.database.connection_string = f"sqlite:///{abs_db_path}"

    # Resolve relative knowledge directory against config file location
    knowledge_dir = config.knowledge.directory
    if knowledge_dir and not Path(knowledge_dir).is_absolute():
        config_dir = Path(args.config).resolve().parent
        config.knowledge.directory = str((config_dir / knowledge_dir).resolve())

    # Initialize adapters
    db = SQLAlchemyAdapter(config.database.connection_string)
    llm = LLMAdapter(
        provider=config.llm.provider,
        model=config.llm.model,
        api_key=config.llm.resolve_api_key(),
        temperature=config.llm.temperature,
        max_output_tokens=config.llm.max_output_tokens,
    )
    tools = create_default_tools(db, config.knowledge.directory)
    context_mgr = ContextManager(config, db)
    playbook_registry = create_default_playbook_registry(
        config.playbooks.directory or None
    )

    # Try to import the executor — if it exists, use it; otherwise run manually
    agent = None
    try:
        from l3_agent.agent.executor import L3Agent
        agent = L3Agent(
            config=config,
            llm=llm,
            db=db,
            tools=tools,
            context_manager=context_mgr,
            playbook_registry=playbook_registry,
        )
    except ImportError:
        pass

    _print_welcome(
        config_path=args.config,
        model=config.llm.model,
        db=config.database.connection_string,
    )

    # Setup prompt_toolkit for input with history
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory

        history_path = Path.home() / ".l3_agent_history"
        session = PromptSession(history=FileHistory(str(history_path)))

        def _get_input() -> str | None:
            try:
                return session.prompt("> ")
            except EOFError:
                return None
            except KeyboardInterrupt:
                return None
    except ImportError:
        # Fallback to plain input
        def _get_input() -> str | None:
            try:
                return input("> ")
            except (EOFError, KeyboardInterrupt):
                return None

    # Conversation loop
    messages: list[dict] = []

    while True:
        try:
            user_input = _get_input()
        except KeyboardInterrupt:
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if user_input is None:
            console.print("\n[dim]Goodbye.[/dim]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            console.print("[dim]Goodbye.[/dim]")
            break
        if user_input.lower() in ("clear", "reset"):
            messages = []
            console.print("[dim]Conversation cleared.[/dim]\n")
            continue

        # Run analysis
        if agent is not None:
            messages = _run_analysis(agent, user_input, messages)
        else:
            # Minimal fallback when executor is not yet implemented
            _run_manual_analysis(
                question=user_input,
                messages=messages,
                llm=llm,
                tools=tools,
                context_mgr=context_mgr,
                playbook_registry=playbook_registry,
                config=config,
            )


def _run_manual_analysis(
    question: str,
    messages: list[dict],
    llm,
    tools,
    context_mgr,
    playbook_registry,
    config,
):
    """Fallback analysis loop when no executor is available.

    Runs the agent loop manually: build prompt, call LLM, execute tools,
    repeat until the LLM stops requesting tools.
    """
    from l3_agent.agent.planner import build_planning_prompt, format_plan_for_injection

    today = date.today().isoformat()

    # Detect playbook
    playbook = playbook_registry.detect_playbook(question)
    playbook_text = playbook.content if playbook else ""

    # Build system prompt
    system_prompt = context_mgr.build_system_prompt(
        question=question,
        today=today,
        playbook_text=playbook_text,
    )

    # Planning phase
    plan_text = ""
    if config.agent.enable_planning:
        console.print("[plan.title][Planning][/plan.title] Generating analysis plan...")
        try:
            plan_prompt = build_planning_prompt(question)
            plan_resp = llm.create_message(
                system="You are a data analysis planner. Output valid JSON only.",
                messages=[{"role": "user", "content": plan_prompt}],
                max_tokens=2048,
                temperature=0,
            )
            for block in plan_resp.get("content", []):
                if block.get("type") == "text":
                    import json as _json
                    try:
                        plan = _json.loads(block["text"])
                        _display_plan(plan)
                        plan_text = format_plan_for_injection(plan)
                    except _json.JSONDecodeError:
                        pass
        except Exception as e:
            console.print(f"  [dim]Planning skipped: {e}[/dim]")

    if plan_text:
        system_prompt += f"\n\n{plan_text}"

    # Add user question to messages
    messages.append({"role": "user", "content": question})

    # Agentic loop
    tool_schemas = tools.get_claude_tools()
    query_count = 0
    start = time.time()
    finished = False

    for _round in range(config.agent.max_rounds):
        with console.status("[bold]Thinking...", spinner="dots"):
            resp = llm.create_message(
                system=system_prompt,
                messages=messages,
                tools=tool_schemas,
            )

        # Process response
        assistant_content = resp.get("content", [])
        messages.append({"role": "assistant", "content": assistant_content})

        stop_reason = resp.get("stop_reason", "end_turn")

        # Handle tool calls
        tool_results = []
        has_text = False
        for block in assistant_content:
            if block.get("type") == "text" and block.get("text", "").strip():
                has_text = True
            elif block.get("type") == "tool_use":
                tool_name = block["name"]
                tool_input = block.get("input", {})
                tool_id = block["id"]

                _display_tool_call(tool_name, tool_input)

                result = tools.execute(tool_name, tool_input)
                _display_tool_result({
                    "success": result.success,
                    "content": result.content,
                    "error": result.error,
                    "duration_ms": result.duration_ms,
                    "tool_name": tool_name,
                })

                if tool_name == "run_sql":
                    query_count += 1

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result.content,
                })

        # Send tool results back
        if tool_results:
            messages.append({"role": "user", "content": tool_results})

        # If stop reason is end_turn or no more tool calls, display final text
        if stop_reason == "end_turn" or not tool_results:
            for block in assistant_content:
                if block.get("type") == "text" and block.get("text", "").strip():
                    _display_streaming_text(block["text"])
            finished = True
            break

    # Force summarize if we hit max rounds without a final answer
    if not finished:
        console.print("[dim]Reached max rounds, forcing summary...[/dim]")
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
            with console.status("[bold]Summarizing...", spinner="dots"):
                resp = llm.create_message(
                    system=system_prompt,
                    messages=messages,
                )
            for block in resp.get("content", []):
                if block.get("type") == "text" and block.get("text", "").strip():
                    _display_streaming_text(block["text"])
        except Exception as e:
            console.print(f"[tool.error]Summary failed: {e}[/tool.error]")

    duration = time.time() - start
    _display_footer(None, query_count, duration)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="l3-agent",
        description="L3 Autonomous Data Agent — interactive data analysis in your terminal.",
    )
    parser.add_argument(
        "--config", "-c",
        help="Path to config.yaml file",
    )
    parser.add_argument(
        "--model", "-m",
        help="Override the LLM model (e.g. claude-sonnet-4-20250514)",
    )

    subparsers = parser.add_subparsers(dest="command")

    # demo
    demo_parser = subparsers.add_parser(
        "demo",
        help="Run the interactive demo with a bundled SQLite database",
    )

    # index
    index_parser = subparsers.add_parser(
        "index",
        help="Scan a directory of .md files and generate index.json",
    )
    index_parser.add_argument(
        "directory",
        help="Path to the knowledge directory to index",
    )

    # check
    check_parser = subparsers.add_parser(
        "check",
        help="Validate configuration (database, LLM, knowledge)",
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Main CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "demo":
        cmd_demo(args)
    elif args.command == "index":
        cmd_index(args)
    elif args.command == "check":
        if not args.config:
            console.print("[red]--config is required for the check command.[/red]")
            sys.exit(1)
        cmd_check(args)
    elif args.config:
        cmd_repl(args)
    else:
        parser.print_help()
        console.print(
            "\n[dim]Quick start: l3-agent demo  |  "
            "Custom: l3-agent --config config.yaml[/dim]"
        )


if __name__ == "__main__":
    main()
