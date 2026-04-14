---
name: exploratory
description: Open-ended exploratory analysis
triggers: [explore, investigate, "tell me about", overview, "deep dive"]
---

# Exploratory Analysis Playbook

You are conducting an open-ended exploratory analysis. This is a lightweight
framework — follow it to stay structured without over-constraining discovery.

## Step 1: Understand the Question
- Restate the user's question in your own words.
- Identify what a "good answer" would look like: a number, a trend, a ranking,
  a comparison, or a narrative?
- List the data you will need and where to find it.

## Step 2: Data Discovery
- Use discover_tables and get_table_schema to survey available data.
- Check data freshness: what is the most recent date in the relevant tables?
- Identify potential join keys and any data quality gotchas (nulls, duplicates,
  missing partitions).

## Step 3: Iterative Analysis
- Start with the simplest query that addresses the question.
- Examine the results and decide what to explore next.
- Each iteration should either answer a sub-question or raise a sharper one.
- Keep a running mental model of the emerging answer.
- **Discipline**: resist the urge to boil the ocean. Stay focused on the
  user's question. Tangential findings can be noted but should not derail
  the main analysis.

## Step 4: Synthesize and Conclude
- Summarize your findings in plain language.
- Lead with the answer, then provide supporting evidence.
- Quantify: include the key numbers that support your conclusion.
- Note limitations: data gaps, assumptions, caveats.
- If the data does not support a clear answer, say so — and suggest what
  additional data or analysis would be needed.
