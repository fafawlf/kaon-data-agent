#!/usr/bin/env python3
"""
Kaon Data Agent (KDA) HTML Report Generator.

Takes an analysis result dict (from L3Agent.analyze()) and produces a
self-contained, dark-themed HTML report suitable for local viewing or
GitHub Pages hosting.

Design system adapted from FlowGPT production report generator.
"""
from __future__ import annotations

import html as html_lib
import os
import re
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Inline markdown helpers
# ---------------------------------------------------------------------------

def _inline_md(text: str) -> str:
    """Handle inline markdown: **bold**, *italic*, `code`."""
    escaped = html_lib.escape(text)
    # Bold
    escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
    # Italic
    escaped = re.sub(r'\*(.+?)\*', r'<em>\1</em>', escaped)
    # Inline code
    escaped = re.sub(r'`(.+?)`', r'<code>\1</code>', escaped)
    return escaped


# ---------------------------------------------------------------------------
# Markdown to HTML converter
# ---------------------------------------------------------------------------

def _md_to_html(md_text: str) -> str:
    """Convert markdown text to HTML.

    Handles: ## / ### headers, **bold**, *italic*, `code`, ``` code blocks,
    - / * unordered lists, 1. ordered lists, > blockquotes, | pipe tables |.
    """
    if not md_text or not md_text.strip():
        return ""

    lines = md_text.split("\n")
    out: list[str] = []
    in_list = False
    list_tag = "ul"
    in_code_block = False
    code_lang = ""
    code_lines: list[str] = []
    in_blockquote = False
    bq_lines: list[str] = []
    in_table = False
    table_lines: list[str] = []

    def _flush_list():
        nonlocal in_list
        if in_list:
            out.append(f"</{list_tag}>")
            in_list = False

    def _flush_blockquote():
        nonlocal in_blockquote, bq_lines
        if in_blockquote:
            content = "\n".join(bq_lines)
            out.append(
                '<div class="blockquote-card">'
                f"<div class=\"bq-content\">{_inline_md(content)}</div>"
                "</div>"
            )
            in_blockquote = False
            bq_lines = []

    def _flush_table():
        nonlocal in_table, table_lines
        if in_table and table_lines:
            out.append(_render_pipe_table(table_lines))
            in_table = False
            table_lines = []

    for line in lines:
        stripped = line.strip()

        # --- Fenced code blocks ---
        if stripped.startswith("```"):
            if not in_code_block:
                _flush_list()
                _flush_blockquote()
                _flush_table()
                in_code_block = True
                code_lang = stripped[3:].strip()
                code_lines = []
            else:
                lang_cls = "sql" if code_lang.lower() == "sql" else ""
                code_text = "\n".join(code_lines)
                if lang_cls == "sql":
                    highlighted = _sql_highlight(code_text)
                else:
                    highlighted = html_lib.escape(code_text)
                out.append(
                    f'<pre class="code-block {lang_cls}">{highlighted}</pre>'
                )
                in_code_block = False
                code_lang = ""
                code_lines = []
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # --- Pipe tables ---
        if stripped.startswith("|") and stripped.endswith("|"):
            _flush_list()
            _flush_blockquote()
            # Check if this is a separator line
            if re.match(r"^\|[\s\-:|]+\|$", stripped):
                if in_table:
                    table_lines.append(stripped)
                continue
            if not in_table:
                in_table = True
                table_lines = []
            table_lines.append(stripped)
            continue
        else:
            _flush_table()

        # --- Blockquotes ---
        if stripped.startswith("> "):
            _flush_list()
            _flush_table()
            if not in_blockquote:
                in_blockquote = True
                bq_lines = []
            bq_lines.append(stripped[2:])
            continue
        elif stripped == ">":
            if in_blockquote:
                bq_lines.append("")
            continue
        else:
            _flush_blockquote()

        # --- Empty line ---
        if not stripped:
            _flush_list()
            continue

        # --- Headers ---
        if stripped.startswith("### "):
            _flush_list()
            out.append(f"<h3>{_inline_md(stripped[4:])}</h3>")
            continue
        if stripped.startswith("## "):
            _flush_list()
            out.append(f"<h2>{_inline_md(stripped[3:])}</h2>")
            continue
        if stripped.startswith("# "):
            _flush_list()
            out.append(f"<h1>{_inline_md(stripped[2:])}</h1>")
            continue

        # --- Unordered list ---
        if stripped.startswith("- ") or stripped.startswith("* "):
            _flush_blockquote()
            _flush_table()
            if not in_list or list_tag != "ul":
                _flush_list()
                list_tag = "ul"
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline_md(stripped[2:])}</li>")
            continue

        # --- Ordered list ---
        m = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if m:
            _flush_blockquote()
            _flush_table()
            if not in_list or list_tag != "ol":
                _flush_list()
                list_tag = "ol"
                out.append("<ol>")
                in_list = True
            out.append(f"<li>{_inline_md(m.group(2))}</li>")
            continue

        # --- Paragraph ---
        _flush_list()
        out.append(f"<p>{_inline_md(stripped)}</p>")

    # Flush any remaining state
    _flush_list()
    _flush_blockquote()
    _flush_table()
    if in_code_block:
        code_text = "\n".join(code_lines)
        out.append(f'<pre class="code-block">{html_lib.escape(code_text)}</pre>')

    return "\n".join(out)


def _render_pipe_table(lines: list[str]) -> str:
    """Render a pipe-delimited markdown table into an HTML table."""
    if not lines:
        return ""

    def _split_row(line: str) -> list[str]:
        cells = line.strip().strip("|").split("|")
        return [c.strip() for c in cells]

    headers = _split_row(lines[0])
    rows = [_split_row(l) for l in lines[1:] if not re.match(r"^[\s\-:|]+$", l.strip().strip("|"))]

    h = '<div class="table-wrap"><table class="data-table"><thead><tr>'
    for col in headers:
        h += f"<th>{_inline_md(col)}</th>"
    h += "</tr></thead><tbody>"
    for row in rows:
        h += "<tr>"
        for i, val in enumerate(row):
            cls = ""
            try:
                fv = float(val.replace("%", "").replace(",", "").replace("$", "").replace("+", ""))
                col_name = headers[i].lower() if i < len(headers) else ""
                if any(k in col_name for k in ["change", "diff", "delta", "growth", "pct"]):
                    cls = ' class="positive"' if fv > 0 else ' class="negative"' if fv < 0 else ""
            except (ValueError, IndexError):
                pass
            h += f"<td{cls}>{_inline_md(val)}</td>"
        h += "</tr>"
    h += "</tbody></table></div>"
    return h


# ---------------------------------------------------------------------------
# SQL syntax highlighting
# ---------------------------------------------------------------------------

def _sql_highlight(sql_text: str) -> str:
    """Syntax-highlight SQL: keywords in red, strings in blue, comments in gray."""
    keywords = [
        "SELECT", "FROM", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER",
        "FULL", "CROSS", "ON", "AND", "OR", "NOT", "IN", "AS", "GROUP BY",
        "ORDER BY", "HAVING", "LIMIT", "OFFSET", "WITH", "CASE", "WHEN",
        "THEN", "ELSE", "END", "DISTINCT", "COUNT", "SUM", "AVG", "MIN",
        "MAX", "ROUND", "BETWEEN", "LIKE", "IS", "NULL", "DESC", "ASC",
        "OVER", "PARTITION BY", "UNION", "ALL", "EXISTS", "DESCRIBE",
        "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "TABLE",
        "INTO", "VALUES", "SET", "IF", "IFNULL", "COALESCE", "CAST",
        "CONCAT", "SQRT", "ABS", "TRUE", "FALSE", "DATE", "INTERVAL",
        "EXTRACT", "SUBSTRING", "REPLACE", "TRIM", "UPPER", "LOWER",
        "ROW_NUMBER", "RANK", "DENSE_RANK", "LAG", "LEAD", "FIRST_VALUE",
        "LAST_VALUE", "ROWS", "RANGE", "UNBOUNDED", "PRECEDING", "FOLLOWING",
        "CURRENT", "ROW",
    ]
    escaped = html_lib.escape(sql_text)
    for kw in sorted(keywords, key=len, reverse=True):
        pattern = re.compile(r"\b(" + kw + r")\b", re.IGNORECASE)
        escaped = pattern.sub(r'<span class="kw">\1</span>', escaped)
    # Strings
    escaped = re.sub(r"('(?:[^'\\]|\\.)*')", r'<span class="str">\1</span>', escaped)
    # Comments
    escaped = re.sub(
        r"(--.*?)$", r'<span class="cmt">\1</span>', escaped, flags=re.MULTILINE
    )
    return escaped


# ---------------------------------------------------------------------------
# Data table rendering
# ---------------------------------------------------------------------------

def _render_data_table(result_text: str, max_rows: int = 50) -> str:
    """Parse a text table from query results and render as an HTML table.

    Handles both pipe-delimited (| col | val |) and space-delimited formats.
    """
    if not result_text or not result_text.strip():
        return '<div class="empty-result">No data returned</div>'

    text = result_text.strip()

    # Error messages
    if text.lower().startswith("error") or "execution error" in text.lower():
        return f'<div class="error-result">{html_lib.escape(text)}</div>'

    # Empty results
    if text.lower().startswith("empty") or "no rows" in text.lower() or "0 rows" in text.lower():
        return '<div class="empty-result">Query returned empty results</div>'

    lines = text.split("\n")

    # Detect pipe-delimited tables
    pipe_lines = [l for l in lines if l.strip().startswith("|")]
    if len(pipe_lines) >= 2:
        return _render_pipe_result_table(pipe_lines, max_rows)

    # Space-delimited fallback
    return _render_space_delimited_table(lines, max_rows)


def _render_pipe_result_table(lines: list[str], max_rows: int) -> str:
    """Render pipe-delimited result table."""
    def _split(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    # Find header and data lines, skip separator
    data_lines = [l for l in lines if not re.match(r"^\|[\s\-:+|]+\|$", l.strip())]
    if not data_lines:
        return f'<pre class="result-pre">{html_lib.escape(chr(10).join(lines))}</pre>'

    headers = _split(data_lines[0])
    rows = [_split(l) for l in data_lines[1:]]

    return _build_html_table(headers, rows, max_rows)


def _render_space_delimited_table(lines: list[str], max_rows: int) -> str:
    """Render space-delimited result table."""
    if len(lines) < 2:
        return f'<pre class="result-pre">{html_lib.escape(chr(10).join(lines))}</pre>'

    headers = lines[0].split()
    rows = []
    for line in lines[1:]:
        if line.strip().startswith("..."):
            continue
        vals = line.split()
        if len(vals) > len(headers):
            diff = len(vals) - len(headers)
            merged_first = " ".join(vals[: diff + 1])
            vals = [merged_first] + vals[diff + 1 :]
        if vals:
            rows.append(vals)

    if not headers:
        return f'<pre class="result-pre">{html_lib.escape(chr(10).join(lines))}</pre>'

    return _build_html_table(headers, rows, max_rows)


def _build_html_table(headers: list[str], rows: list[list[str]], max_rows: int) -> str:
    """Build an HTML table from headers and rows."""
    total_rows = len(rows)
    display_rows = rows[:max_rows]

    h = '<div class="table-wrap"><table class="data-table"><thead><tr>'
    for col in headers:
        h += f"<th>{html_lib.escape(col)}</th>"
    h += "</tr></thead><tbody>"

    for row in display_rows:
        h += "<tr>"
        for i, val in enumerate(row):
            cls = ""
            try:
                fv = float(
                    str(val)
                    .replace("%", "")
                    .replace(",", "")
                    .replace("$", "")
                    .replace("+", "")
                )
                col_name = headers[i].lower() if i < len(headers) else ""
                if any(
                    k in col_name
                    for k in ["change", "diff", "delta", "z_score", "z_", "growth", "pct_"]
                ):
                    cls = (
                        ' class="positive"'
                        if fv > 0
                        else ' class="negative"' if fv < 0 else ""
                    )
            except (ValueError, IndexError):
                pass
            h += f"<td{cls}>{html_lib.escape(str(val))}</td>"
        h += "</tr>"

    if total_rows > max_rows:
        h += (
            f'<tr><td colspan="{len(headers)}" class="truncated">'
            f"... {total_rows} rows total</td></tr>"
        )
    h += "</tbody></table></div>"
    h += f'<div class="row-count">{total_rows} row{"s" if total_rows != 1 else ""}</div>'
    return h


# ---------------------------------------------------------------------------
# Hero / executive summary extraction
# ---------------------------------------------------------------------------

def _extract_hero(answer: str) -> str:
    """Extract executive summary from answer text.

    Takes the first blockquote (> ...) or, failing that, the first paragraph.
    """
    if not answer:
        return ""

    lines = answer.strip().split("\n")

    # Try to find a blockquote
    bq_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("> "):
            bq_lines.append(stripped[2:])
        elif bq_lines:
            break

    if bq_lines:
        return " ".join(bq_lines)

    # Fall back to first non-empty, non-heading paragraph
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            continue
        if stripped.startswith("|"):
            continue
        return stripped

    return ""


# ---------------------------------------------------------------------------
# Main report generator
# ---------------------------------------------------------------------------

def generate_report(
    result: dict,
    title: str = "",
    output_path: str | None = None,
) -> str:
    """Generate a self-contained HTML report from an L3Agent analysis result.

    Args:
        result: dict from L3Agent.analyze() with keys:
            answer, confidence, confidence_reason, queries, ruled_out,
            evidence_summary, plan, tool_stats
        title: optional report title (auto-generated from question if empty)
        output_path: if provided, write the HTML to this file path

    Returns:
        The complete HTML string.
    """
    # Extract fields with safe defaults
    answer = result.get("answer", "")
    confidence = result.get("confidence", "medium")
    confidence_reason = result.get("confidence_reason", "")
    queries = result.get("queries", [])
    ruled_out = result.get("ruled_out", [])
    evidence_summary = result.get("evidence_summary", "")
    plan = result.get("plan", {})
    tool_stats = result.get("tool_stats", {})

    # Auto-generate title
    if not title:
        question = result.get("question", "")
        if question:
            title = question[:80] + ("..." if len(question) > 80 else "")
        else:
            title = "Data Analysis Report"

    # Confidence styling
    conf_color = {
        "high": "#22c55e",
        "medium": "#f59e0b",
        "low": "#ef4444",
    }.get(confidence, "#6b7280")
    conf_label = {
        "high": "High",
        "medium": "Medium",
        "low": "Low",
    }.get(confidence, "Unknown")

    # Compute stats
    total_queries = len(queries)
    ok_count = sum(
        1
        for q in queries
        if q.get("success", True)
        and not str(q.get("result_full", "")).lower().startswith("error")
    )
    duration = tool_stats.get("total_duration_secs", 0)
    if not duration:
        duration = sum(q.get("duration_ms", 0) for q in queries) / 1000

    # Confidence value CSS class
    conf_value_cls = {
        "High": "green",
        "Medium": "orange",
        "Low": "red",
    }.get(conf_label, "orange")

    # Hero summary
    hero_text = _extract_hero(answer)

    # Build sections
    investigation_html = _build_investigation(queries)
    conclusion_html = _md_to_html(answer)
    evidence_html = _build_evidence(
        confidence, conf_color, conf_label,
        confidence_reason, evidence_summary, ruled_out,
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = _TEMPLATE.format(
        title=html_lib.escape(title),
        conf_color=conf_color,
        conf_label=conf_label,
        conf_value_cls=conf_value_cls,
        total_queries=total_queries,
        ok_count=ok_count,
        duration=f"{duration:.1f}",
        hero_text=_inline_md(hero_text) if hero_text else "",
        hero_display="block" if hero_text else "none",
        investigation_html=investigation_html,
        conclusion_html=conclusion_html,
        evidence_html=evidence_html,
        generated_time=now,
    )

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

    return html


# ---------------------------------------------------------------------------
# Investigation section builder
# ---------------------------------------------------------------------------

def _build_investigation(queries: list[dict]) -> str:
    """Build the investigation HTML from the list of query steps."""
    if not queries:
        return '<p class="empty-note">No queries were executed.</p>'

    parts: list[str] = []
    for i, q in enumerate(queries):
        step_num = i + 1
        sql = q.get("sql", "")
        reasoning = q.get("reasoning", "")
        result_text = q.get("result_full", q.get("result", ""))
        is_error = not q.get("success", True) or str(result_text).lower().startswith("error")
        is_describe = sql.strip().upper().startswith("DESCRIBE") or sql.strip().upper().startswith("SHOW")

        step_cls = "step-error" if is_error else "step-describe" if is_describe else "step-analysis"
        step_label = "Error" if is_error else "Schema" if is_describe else "Analysis"

        sql_id = f"sql_{step_num}"

        # Reasoning block
        reasoning_html = ""
        if reasoning:
            paragraphs = reasoning.strip().split("\n\n")
            r_parts = []
            for p in paragraphs:
                content = " ".join(l.strip() for l in p.strip().split("\n") if l.strip())
                if content:
                    r_parts.append(f"<p>{_inline_md(content)}</p>")
            reasoning_html = "\n".join(r_parts)

        # Result rendering
        result_html = _render_data_table(str(result_text)) if result_text else ""

        # Reason / title
        reason = q.get("reason", q.get("purpose", ""))

        parts.append(f'''
    <div class="analysis-card {step_cls}" id="step-{step_num}">
      <div class="card-header">
        <div class="step-badge">{step_num}</div>
        <div class="step-meta">
          <span class="step-label">{step_label}</span>
          <h3 class="step-title">{html_lib.escape(reason)}</h3>
        </div>
      </div>
      {f'<div class="reasoning-block">{reasoning_html}</div>' if reasoning_html else ""}
      <details class="sql-details"{"" if is_describe else " open"}>
        <summary>SQL Query <button class="copy-btn" onclick="copySQL('{sql_id}')">Copy</button></summary>
        <pre class="sql-body" id="{sql_id}">{_sql_highlight(sql)}</pre>
      </details>
      <div class="result-section">
        <div class="result-label">Result</div>
        {result_html}
      </div>
    </div>''')

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Evidence section builder
# ---------------------------------------------------------------------------

def _build_evidence(
    confidence: str,
    conf_color: str,
    conf_label: str,
    confidence_reason: str,
    evidence_summary: str,
    ruled_out: list[str],
) -> str:
    """Build the evidence / confidence section."""
    ruled_out_html = ""
    if ruled_out:
        items = "".join(f"<li>{html_lib.escape(r)}</li>" for r in ruled_out)
        ruled_out_html = f"""
      <div class="ruled-out">
        <h4>Ruled Out Hypotheses</h4>
        <ul>{items}</ul>
      </div>"""

    evidence_block = ""
    if evidence_summary:
        evidence_block = f"""
      <div class="evidence-block">
        <h4>Evidence Summary</h4>
        <div class="evidence-body">{_md_to_html(evidence_summary)}</div>
      </div>"""

    return f"""
    <div class="confidence-card">
      <h3>Confidence Assessment</h3>
      <p style="margin-bottom:16px">
        <span class="conf-badge" style="border-color:{conf_color}; color:{conf_color}">
          {conf_label}
        </span>
      </p>
      {f'<p class="conf-reason">{html_lib.escape(confidence_reason)}</p>' if confidence_reason else ""}
      {evidence_block}
      {ruled_out_html}
    </div>"""


# ---------------------------------------------------------------------------
# Full HTML template
# ---------------------------------------------------------------------------

_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} - Kaon Data Agent</title>
<style>
:root {{
  --bg: #0f172a; --card: #1e293b; --card2: #273449; --card3: #1a2332;
  --text: #e2e8f0; --text2: #94a3b8; --text3: #64748b;
  --border: #334155; --border2: #475569;
  --accent: #3b82f6; --accent2: #8b5cf6; --accent-dim: #1e3a5f;
  --green: #22c55e; --red: #ef4444; --orange: #f59e0b; --cyan: #06b6d4;
  --code-bg: #0d1117;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  line-height: 1.7;
}}

/* === Layout === */
.page {{ max-width: 960px; margin: 0 auto; padding: 0 20px 60px; }}

/* === Header === */
.header {{
  background: linear-gradient(135deg, #1a2744 0%, #0f172a 50%, #162033 100%);
  border-bottom: 1px solid var(--border); padding: 48px 0 36px;
}}
.header-inner {{ max-width: 960px; margin: 0 auto; padding: 0 20px; }}
.header h1 {{
  font-size: 24px; font-weight: 700; margin-bottom: 14px; line-height: 1.4;
}}
.header h1 .hl {{ color: var(--accent); }}
.header-tags {{ display: flex; gap: 10px; flex-wrap: wrap; }}
.tag {{
  background: var(--card2); padding: 5px 14px; border-radius: 6px;
  font-size: 13px; color: var(--text2);
}}
.tag.conf {{ border: 1px solid {conf_color}; color: {conf_color}; }}

/* === Sticky Nav === */
.nav {{
  position: sticky; top: 0; z-index: 100;
  background: rgba(15, 23, 42, 0.95); backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--border); padding: 10px 0;
}}
.nav-inner {{
  max-width: 960px; margin: 0 auto; padding: 0 20px;
  display: flex; gap: 6px; overflow-x: auto;
}}
.nav-btn {{
  padding: 7px 16px; border-radius: 6px; font-size: 13px;
  color: var(--text2); background: var(--card); border: 1px solid var(--border);
  cursor: pointer; white-space: nowrap; text-decoration: none;
  transition: all 0.15s ease;
}}
.nav-btn:hover, .nav-btn.active {{
  background: var(--accent); color: white; border-color: var(--accent);
}}

/* === Summary Bar === */
.summary-bar {{
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 16px; margin-bottom: 32px; margin-top: 24px;
}}
.summary-item {{
  background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; padding: 18px 20px;
}}
.summary-item .label {{
  font-size: 11px; color: var(--text3); text-transform: uppercase;
  letter-spacing: 0.5px; margin-bottom: 6px;
}}
.summary-item .value {{ font-size: 24px; font-weight: 700; }}
.value.green {{ color: var(--green); }}
.value.orange {{ color: var(--orange); }}
.value.red {{ color: var(--red); }}
.value.blue {{ color: var(--accent); }}

/* === Hero Card === */
.hero-card {{
  background: linear-gradient(135deg, var(--card2) 0%, var(--card) 100%);
  border: 1px solid var(--accent-dim); border-left: 4px solid var(--accent);
  border-radius: 12px; padding: 24px 28px; margin-bottom: 32px;
  font-size: 16px; line-height: 1.8;
}}
.hero-card strong {{ color: #f1f5f9; }}
.hero-card em {{ color: var(--cyan); font-style: normal; }}
.hero-card code {{
  background: var(--code-bg); padding: 2px 6px;
  border-radius: 3px; font-size: 14px; color: var(--cyan);
}}

/* === Section Headings === */
.section-heading {{
  font-size: 20px; font-weight: 700; margin: 40px 0 20px;
  padding-bottom: 12px; border-bottom: 1px solid var(--border);
  color: var(--text);
}}
.section-heading .icon {{ margin-right: 8px; }}

/* === Analysis Cards === */
.analysis-card {{
  background: var(--card); border: 1px solid var(--border);
  border-radius: 12px; margin-bottom: 20px; overflow: hidden;
}}
.analysis-card.step-describe {{ opacity: 0.7; }}
.analysis-card.step-describe .card-header {{ background: var(--card3); }}
.analysis-card.step-error .card-header {{
  background: #2d1b1b; border-left: 3px solid var(--red);
}}
.analysis-card.step-analysis .card-header {{
  border-left: 3px solid var(--accent);
}}

.card-header {{
  display: flex; align-items: flex-start; gap: 14px;
  padding: 18px 20px; background: var(--card2);
}}
.step-badge {{
  width: 32px; height: 32px; border-radius: 50%;
  background: var(--accent); color: white; font-size: 14px;
  font-weight: 700; display: flex; align-items: center;
  justify-content: center; flex-shrink: 0;
}}
.step-error .step-badge {{ background: var(--red); }}
.step-describe .step-badge {{ background: var(--text3); }}
.step-meta {{ flex: 1; }}
.step-label {{
  font-size: 11px; color: var(--text3); text-transform: uppercase;
  letter-spacing: 0.5px;
}}
.step-title {{ font-size: 16px; font-weight: 600; margin-top: 2px; }}

/* Reasoning */
.reasoning-block {{
  padding: 20px 24px; border-bottom: 1px solid var(--border);
  font-size: 15px; line-height: 1.8; color: var(--text);
}}
.reasoning-block p {{ margin-bottom: 10px; }}
.reasoning-block p:last-child {{ margin-bottom: 0; }}
.reasoning-block strong {{ color: #f1f5f9; }}
.reasoning-block em {{ color: var(--cyan); font-style: normal; }}

/* SQL */
.sql-details {{ border-top: 1px solid var(--border); }}
.sql-details summary {{
  padding: 10px 20px; font-size: 13px; color: var(--text3);
  cursor: pointer; display: flex; align-items: center;
  justify-content: space-between;
}}
.sql-details summary:hover {{ background: var(--card2); }}
.sql-body {{
  padding: 16px 20px; background: var(--code-bg);
  font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 12.5px; line-height: 1.6; overflow-x: auto;
  white-space: pre-wrap; word-break: break-word; margin: 0;
}}
.sql-body .kw {{ color: #ff7b72; font-weight: 600; }}
.sql-body .str {{ color: #a5d6ff; }}
.sql-body .cmt {{ color: #8b949e; }}
.copy-btn {{
  background: var(--card2); border: 1px solid var(--border);
  color: var(--text3); padding: 2px 10px; border-radius: 4px;
  cursor: pointer; font-size: 11px; margin-left: auto;
  transition: all 0.15s ease;
}}
.copy-btn:hover {{ background: var(--accent); color: white; }}

/* Result */
.result-section {{ padding: 0; }}
.result-label {{
  padding: 10px 20px 6px; font-size: 11px; color: var(--text3);
  text-transform: uppercase; letter-spacing: 0.5px;
}}
.table-wrap {{ overflow-x: auto; padding: 0 0 8px; }}
.data-table {{
  width: 100%; border-collapse: collapse;
  font-size: 12.5px; font-family: 'JetBrains Mono', monospace;
}}
.data-table th {{
  background: var(--code-bg); padding: 8px 14px; text-align: left;
  border-bottom: 1px solid var(--border); color: var(--text2);
  font-weight: 600; white-space: nowrap; position: sticky; top: 0;
}}
.data-table td {{
  padding: 7px 14px; border-bottom: 1px solid #1a2332; white-space: nowrap;
}}
.data-table tr:hover {{ background: var(--card2); }}
.data-table td.positive {{ color: var(--green); font-weight: 600; }}
.data-table td.negative {{ color: var(--red); font-weight: 600; }}
.error-result {{
  padding: 16px 20px; color: var(--red); font-size: 13px;
  font-family: monospace;
}}
.empty-result {{
  padding: 16px 20px; color: var(--orange); font-size: 13px;
}}
.result-pre {{
  padding: 16px 20px; font-size: 12px; color: var(--text2);
  white-space: pre-wrap;
}}
.truncated {{ text-align: center; color: var(--text3); font-style: italic; }}
.row-count {{
  padding: 4px 20px 12px; font-size: 11px; color: var(--text3);
  text-align: right;
}}

/* === Conclusion Section === */
.conclusion-area {{
  background: var(--card); border: 1px solid var(--border);
  border-radius: 12px; padding: 28px 28px; margin-bottom: 24px;
}}
.conclusion-area h1 {{ font-size: 20px; margin: 20px 0 12px; color: var(--accent); }}
.conclusion-area h2 {{ font-size: 18px; margin: 20px 0 10px; color: var(--accent); }}
.conclusion-area h3 {{ font-size: 16px; margin: 16px 0 8px; color: var(--text); }}
.conclusion-area strong {{ color: #f1f5f9; }}
.conclusion-area em {{ color: var(--cyan); font-style: italic; }}
.conclusion-area ul, .conclusion-area ol {{ margin: 8px 0 8px 24px; }}
.conclusion-area li {{ margin-bottom: 6px; }}
.conclusion-area p {{ margin-bottom: 10px; line-height: 1.8; }}
.conclusion-area code {{
  background: var(--code-bg); padding: 2px 6px;
  border-radius: 3px; font-size: 13px; color: var(--cyan);
}}
.conclusion-area .code-block {{
  background: var(--code-bg); padding: 16px 20px;
  border-radius: 8px; overflow-x: auto; margin: 12px 0;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 12.5px; line-height: 1.6; white-space: pre-wrap;
}}
.conclusion-area .code-block.sql .kw {{ color: #ff7b72; font-weight: 600; }}
.conclusion-area .code-block.sql .str {{ color: #a5d6ff; }}
.conclusion-area .code-block.sql .cmt {{ color: #8b949e; }}

/* Blockquote cards */
.blockquote-card {{
  background: var(--card2); border-left: 3px solid var(--accent2);
  border-radius: 0 8px 8px 0; padding: 16px 20px; margin: 12px 0;
}}
.bq-content {{ color: var(--text); font-size: 15px; line-height: 1.7; }}
.bq-content strong {{ color: #f1f5f9; }}

/* === Confidence Card === */
.confidence-card {{
  background: var(--card); border: 1px solid var(--border);
  border-radius: 12px; padding: 28px; margin-top: 16px;
}}
.confidence-card h3 {{ font-size: 18px; margin-bottom: 16px; }}
.conf-badge {{
  display: inline-block; padding: 5px 18px; border-radius: 20px;
  font-weight: 700; font-size: 14px; border: 2px solid;
}}
.conf-reason {{
  color: var(--text2); font-size: 14px; line-height: 1.7;
  margin-bottom: 16px;
}}
.evidence-block {{ margin-top: 16px; }}
.evidence-block h4 {{
  font-size: 14px; color: var(--text2); margin-bottom: 8px;
  text-transform: uppercase; letter-spacing: 0.3px;
}}
.evidence-body {{ font-size: 14px; line-height: 1.7; }}
.evidence-body p {{ margin-bottom: 6px; }}
.ruled-out {{ margin-top: 16px; }}
.ruled-out h4 {{
  font-size: 14px; color: var(--text3); margin-bottom: 8px;
  text-transform: uppercase; letter-spacing: 0.3px;
}}
.ruled-out ul {{ margin-left: 20px; }}
.ruled-out li {{
  color: var(--text3); font-size: 13px; margin-bottom: 4px;
  text-decoration: line-through; opacity: 0.8;
}}

.empty-note {{
  color: var(--text3); font-size: 14px; font-style: italic;
  padding: 20px;
}}

/* === Footer === */
.footer {{
  text-align: center; color: var(--text3); font-size: 12px;
  padding: 32px 0; border-top: 1px solid var(--border); margin-top: 40px;
}}

/* === Responsive === */
@media (max-width: 768px) {{
  .summary-bar {{ grid-template-columns: repeat(2, 1fr); }}
  .nav-inner {{ padding: 0 12px; }}
  .header h1 {{ font-size: 20px; }}
  .hero-card {{ padding: 18px 20px; font-size: 15px; }}
  .analysis-card .card-header {{ padding: 14px 16px; }}
  .reasoning-block {{ padding: 16px 18px; }}
  .conclusion-area {{ padding: 20px 18px; }}
}}
@media (max-width: 480px) {{
  .summary-bar {{ grid-template-columns: 1fr 1fr; gap: 10px; }}
  .header-tags {{ gap: 6px; }}
  .tag {{ font-size: 12px; padding: 4px 10px; }}
}}
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div class="header-inner">
    <h1><span class="hl">Analysis</span> &mdash; {title}</h1>
    <div class="header-tags">
      <span class="tag conf">Confidence: {conf_label}</span>
      <span class="tag">{total_queries} queries</span>
      <span class="tag">{duration}s</span>
      <span class="tag">{generated_time}</span>
    </div>
  </div>
</div>

<!-- Sticky Nav -->
<div class="nav"><div class="nav-inner">
  <a class="nav-btn" href="#investigation">Investigation</a>
  <a class="nav-btn" href="#conclusion">Conclusion</a>
  <a class="nav-btn" href="#evidence">Evidence</a>
</div></div>

<div class="page">

<!-- Summary Bar -->
<div class="summary-bar">
  <div class="summary-item">
    <div class="label">Queries</div>
    <div class="value blue">{total_queries}</div>
  </div>
  <div class="summary-item">
    <div class="label">Successful</div>
    <div class="value green">{ok_count}</div>
  </div>
  <div class="summary-item">
    <div class="label">Confidence</div>
    <div class="value {conf_value_cls}">{conf_label}</div>
  </div>
  <div class="summary-item">
    <div class="label">Duration</div>
    <div class="value">{duration}s</div>
  </div>
</div>

<!-- Hero -->
<div class="hero-card" style="display:{hero_display}">
  {hero_text}
</div>

<!-- Investigation -->
<h2 class="section-heading" id="investigation">
  <span class="icon">&#128269;</span> Investigation &mdash; {total_queries} Steps
</h2>
{investigation_html}

<!-- Conclusion -->
<h2 class="section-heading" id="conclusion">
  <span class="icon">&#128200;</span> Conclusion
</h2>
<div class="conclusion-area">
  {conclusion_html}
</div>

<!-- Evidence -->
<h2 class="section-heading" id="evidence">
  <span class="icon">&#128737;</span> Evidence &amp; Confidence
</h2>
{evidence_html}

</div><!-- .page -->

<div class="footer">
  Generated by Kaon Data Agent (KDA) &middot; {generated_time}
</div>

<script>
function copySQL(id) {{
  var el = document.getElementById(id);
  if (!el) return;
  navigator.clipboard.writeText(el.textContent).then(function() {{
    var btn = el.closest('.sql-details').querySelector('.copy-btn');
    if (btn) {{
      btn.textContent = 'Copied';
      setTimeout(function() {{ btn.textContent = 'Copy'; }}, 2000);
    }}
  }});
}}

// Smooth scroll for nav
document.querySelectorAll('.nav-btn').forEach(function(a) {{
  a.addEventListener('click', function(e) {{
    e.preventDefault();
    var target = document.querySelector(a.getAttribute('href'));
    if (target) target.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  }});
}});

// Active nav highlighting on scroll
(function() {{
  var sections = ['investigation', 'conclusion', 'evidence'];
  var buttons = document.querySelectorAll('.nav-btn');
  window.addEventListener('scroll', function() {{
    var scrollPos = window.scrollY + 80;
    var active = '';
    sections.forEach(function(id) {{
      var el = document.getElementById(id);
      if (el && el.offsetTop <= scrollPos) active = id;
    }});
    buttons.forEach(function(btn) {{
      if (btn.getAttribute('href') === '#' + active) {{
        btn.classList.add('active');
      }} else {{
        btn.classList.remove('active');
      }}
    }});
  }});
}})();
</script>

</body>
</html>'''
