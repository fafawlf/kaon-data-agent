from __future__ import annotations

"""
Playbook system for the L3 data agent.

Playbooks are structured analysis frameworks that guide the agent through
domain-specific analytical workflows. They can be loaded from .md files with
YAML frontmatter or registered as built-in string constants.

Each playbook has:
- name: unique identifier
- description: short human-readable summary
- triggers: list of keywords for automatic detection
- content: the markdown body with instructions
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


# ===========================================================================
# Playbook dataclass
# ===========================================================================

@dataclass
class Playbook:
    """A structured analysis playbook."""
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    content: str = ""


# ===========================================================================
# Frontmatter parser
# ===========================================================================

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n(.*)",
    re.DOTALL,
)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown string.

    Returns (metadata_dict, body_text). If no frontmatter is found,
    returns an empty dict and the original text.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw_yaml, body = match.group(1), match.group(2)
    try:
        if yaml is not None:
            meta = yaml.safe_load(raw_yaml) or {}
        else:
            # Simple fallback parser for key: value and key: [a, b, c]
            meta = _parse_yaml_simple(raw_yaml)
    except Exception:
        return {}, text
    return meta, body.strip()


def _parse_yaml_simple(raw: str) -> dict[str, Any]:
    """Minimal YAML-like parser when PyYAML is not installed."""
    result: dict[str, Any] = {}
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # Handle [a, b, c] list syntax
        if value.startswith("[") and value.endswith("]"):
            items = value[1:-1].split(",")
            result[key] = [item.strip().strip("'\"") for item in items if item.strip()]
        else:
            result[key] = value.strip("'\"")
    return result


# ===========================================================================
# PlaybookRegistry
# ===========================================================================

class PlaybookRegistry:
    """Registry that loads and matches playbooks."""

    def __init__(self):
        self._playbooks: dict[str, Playbook] = {}

    # -- Registration -------------------------------------------------------

    def register(self, playbook: Playbook) -> None:
        """Register a single playbook."""
        self._playbooks[playbook.name] = playbook

    def load_directory(self, directory: str) -> int:
        """Load all .md playbook files from a directory.

        Each file must have YAML frontmatter with at least `name` and
        `description`. Returns the number of playbooks loaded.
        """
        loaded = 0
        dir_path = Path(directory)
        if not dir_path.is_dir():
            return 0

        for md_file in sorted(dir_path.glob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8")
            except Exception:
                continue

            meta, body = _parse_frontmatter(text)
            name = meta.get("name", md_file.stem)
            description = meta.get("description", "")
            triggers = meta.get("triggers", [])
            if isinstance(triggers, str):
                triggers = [t.strip() for t in triggers.split(",")]

            playbook = Playbook(
                name=name,
                description=description,
                triggers=triggers,
                content=body,
            )
            self.register(playbook)
            loaded += 1

        return loaded

    # -- Lookup -------------------------------------------------------------

    def get(self, name: str) -> Playbook | None:
        """Get a playbook by exact name."""
        return self._playbooks.get(name)

    @property
    def names(self) -> list[str]:
        return list(self._playbooks.keys())

    def detect_playbook(self, question: str) -> Playbook | None:
        """Detect the best-matching playbook for a user question.

        Matches by counting trigger keyword hits (case-insensitive).
        Returns the playbook with the most hits, or None if no triggers match.
        """
        question_lower = question.lower()
        best: Playbook | None = None
        best_score = 0

        for playbook in self._playbooks.values():
            score = sum(
                1 for trigger in playbook.triggers
                if trigger.lower() in question_lower
            )
            if score > best_score:
                best_score = score
                best = playbook

        return best


# ===========================================================================
# Built-in playbook content
# ===========================================================================

DIAGNOSTIC_PLAYBOOK_CONTENT = """\
# Metric Anomaly Diagnostic Playbook

You are investigating a metric anomaly. Your job is to find the root cause —
not stop at "something dropped," but explain WHY and recommend WHAT TO DO.

Investigate like a real analyst. Keep querying until you find the answer.
You have plenty of query budget — don't be stingy.

## Step 1: Confirm the Anomaly
- Pull 14-30 day trend. Is this a sudden break or gradual drift?
- Quantify: how many standard deviations from the moving average?
- Check related metrics: did they move together or diverge?
  (DAU + Revenue + Engagement moving together = systemic; diverging = targeted)

## Step 2: Dimension Decomposition (the core step)
- Break down by EVERY available dimension: country, platform, channel, user segment.
- For EACH dimension, calculate **contribution percentage**:
  `contribution = segment_change / total_change * 100%`
- Rank segments by contribution. The top 1-2 segments usually explain 80%+ of the change.

**Iron discipline**: NEVER say "segment X dropped." ALWAYS say "segment X contributed
N% of the total decline (from A to B, -C%)." Numbers must be specific.

## Step 3: Volume vs. Rate (critical distinction)
- Is a segment SMALLER (fewer users in it = external cause, e.g. channel stopped sending traffic)?
- Or are users in the segment BEHAVING DIFFERENTLY (same volume, lower rate = internal cause)?
- This distinction determines whether the root cause is external (marketing, partnership, outage)
  or internal (product change, bug, degradation).

Example:
- "Japan DAU dropped 90%" → volume change → external (outage, block, regulatory)
- "iOS conversion rate dropped 3pp but volume unchanged" → rate change → internal (app bug, UI regression)

## Step 4: Root Cause Investigation (do NOT stop at dimension level)
- "Mobile dropped" is NOT a root cause. WHY did mobile drop?
- Drill into the top contributing segment:
  - If channel-driven: which campaign? When did it start? What's the user quality?
  - If country-driven: organic or paid? Only one platform or all? Regulatory change?
  - If user-segment-driven: new vs. returning? Premium vs. free? What changed for them?
- Cross-reference with events: deployments, experiments, marketing changes, external factors.

## Step 5: User Feedback / Qualitative Validation (MANDATORY)

After finding WHO changed quantitatively, scan for qualitative signals:

1. Based on your findings, derive search keywords for user complaints or bug reports:
   - Retention/activity drop → search: "crash", "slow", "error", "freeze", "loading", "not working"
   - Revenue anomaly → search: "payment", "subscribe", "charge", "purchase", "billing"
   - Quality issues → search: "memory", "forget", "repetitive", "quality", "broken"
2. Compare complaint volume/themes before vs. during the anomaly period.
3. **Low complaint volume is itself evidence** — it helps rule out product/technical bugs.

This step provides an independent validation dimension. Skip it only if no
feedback data source is available, and note that explicitly.

## Step 6: Cross-Validation
- Validate using a different data source or angle:
  - Does the timing match precisely?
  - Are unaffected segments truly stable (natural control group)?
  - Does a complementary metric confirm the story?
- If validation fails, go back to Step 2 with a new hypothesis.

## Step 7: Report

**Lead with the conclusion.** The first thing the reader sees must be the answer:
> Root cause: [one sentence]. Impact: [quantified]. Recommendation: [specific action].

Then provide supporting sections:
- **Impact quantification**: magnitude, duration, revenue impact if applicable.
- **Evidence chain**: each conclusion linked to the data that supports it.
- **Recommendations**: ranked by expected impact, specific and actionable.
- **Open questions**: data gaps, things you couldn't verify.

## Disciplines (non-negotiable)
- Every number must have a comparison baseline. "DAU was 1,200" means nothing.
  "DAU was 1,200 vs. 1,560 baseline (-23%)" means everything.
- Don't write all SQL at once — each result should inform the next query.
- If a result looks wrong (0% retention, 200% growth), check your SQL before trusting it.
- Prefer absolute numbers alongside percentages for context.
- If data is insufficient, say so explicitly. Do not speculate.
- **Tables first, then conclusions.** Never state a conclusion without showing the data table first.
"""

AB_EXPERIMENT_PLAYBOOK_CONTENT = """\
# AB Experiment Analysis Playbook

A good AB analysis is NOT "did the metric go up or down." It is
"WHY did it go up/down, and specifically WHICH user behavior changed."

## The Causal Inference Framework (the soul of the analysis)

Before writing any SQL, build a causal hypothesis chain:

```
What the experiment changed (product change)
    ↓
Which user behaviors are directly affected (first-order effects)
    ↓
How those behavior changes propagate to core metrics (causal chain)
    ↓
Which user segments are most sensitive to this change (moderators)
```

**You MUST write out this chain after the baseline step:**
> **Treatment**: [what changed? If background doesn't say, infer from experiment name and mark "inferred"]
> **Directly affected behaviors**: [which interactions change in discoverability/cost/quality?]
> **Causal chain**: [change] → [behavior X changes] → [metric Y changes]
> **Most sensitive segment**: [who is most exposed? who adapts least?]
> **Hypotheses to verify**: [2-3 specific, queryable hypotheses]

ALL subsequent dimension choices and behavioral analysis MUST follow this chain.
Generic dimensions (gender, channel, platform) still get checked but are lower priority.

## Steps

### 1. Experiment Overview + SRM Check
- Group sizes, time range, allocation ratios.
- SRM: chi-squared test on group sizes. If p < 0.01, STOP — SRM invalidates everything.

### 2. Population Profile + Baseline Expectations (MANDATORY)
- Query the experiment population's key characteristics in ONE SQL:
  engagement rate, new user %, paying user %, average session duration.
- Compare to your overall user base to understand WHO is in the experiment:

| Metric | Experiment Users | Overall Baseline | Gap |
|--------|-----------------|-----------------|-----|
| Engaged users % | ?% | ~X% | |
| New users % | ?% | ~Y% | |
| Paying users % | ?% | ~Z% | |

- **Infer the trigger condition** from population composition:
  - Engaged users >> baseline → triggered in core product flow (expect higher retention)
  - New users ≈ 100% → triggered at onboarding (expect lower retention)
  - Matches baseline → triggered site-wide
- **You MUST output baseline expectations**:
  > Based on population characteristics, expected D1 retention is approximately X%.
- **All subsequent comparisons use this baseline as anchor.** Deviations from baseline
  MUST be explained using population data:
  - Wrong: "D1 retention 61%, seems reasonable"
  - Right: "D1 retention 61%. Experiment is 85% engaged users (vs 50% baseline), so
    expected retention ≈ 70%. The 9pp gap may be explained by new user dilution."

### 3. Causal Hypothesis Chain (MANDATORY)
- Write out the chain as described above.
- If the experiment background is incomplete, infer and mark "[inferred]".
- List 2-3 specific hypotheses to test.

### 4. Core Metrics — All Groups (z-test significance)
- Show EVERY group, not just the winner. Readers need to see the full picture.
- For each test vs. control: z-test for proportions, t-test for means.
- Multiple groups: apply **Bonferroni correction** (alpha = 0.05 / number_of_comparisons).
- Report: absolute value, difference in pp, relative %, z-score, p-value.
- Calculate MDE: is the sample large enough to detect the observed effect?
- Look for the **strategy gradient**: from control through each test group,
  is the effect linear or is there a breakpoint?

### 5. Dimension Drill-Down
**Priority: causal chain dimensions FIRST, generic dimensions SECOND.**

First priority (at least half your SQL budget): dimensions on the causal chain.
- What validates or invalidates the causal hypothesis?
- Who is most exposed to the change? Who is most sensitive?

Second priority: generic dimensions for systematic bias check.
- Platform, country, channel, new vs. returning users.
- Not significant? One sentence and move on.

**Every drill-down MUST**:
1. State WHY you're checking this dimension.
2. Show data table with ALL groups.
3. Interpret: does this dimension moderate the treatment effect?

### 6. Behavioral Mechanism Verification (MANDATORY — do not skip)

**Goal: answer "specifically WHAT user behavior changed, causing the metric change."
Skip this step and your analysis is a half-finished product.**

You MUST execute at least one SQL query to verify the causal chain before concluding.

Based on experiment type:
- **UI/layout experiments**: measure each interaction type's usage rate across groups
- **Backend/algorithm experiments**: measure output quality metrics (response time, relevance)
- **Recommendation experiments**: measure funnel conversion at each stage
- **Feature experiments**: measure feature adoption rate and frequency

Verification steps:
1. Directly measure the intermediate behavior across groups.
2. Check transmission: do users with the changed behavior also show the metric change?
3. Rule out alternatives: could another behavior explain the metric change?
4. **Quantify the contribution**: you must be able to write a sentence like:
   > "[Edit button usage] dropped by [34%] in the treatment group, explaining
   > approximately [60%] of the observed retention decline."
   If you cannot produce this sentence, explicitly state:
   "Unable to isolate the specific behavioral mechanism."

### 7. Cross-Validation + Conclusion
- Check for novelty effects: plot the treatment effect by day. Is it stable or decaying?
- Cross-validate with related metrics.
- Actively look for counter-evidence.

### Report Structure

**Lead with the conclusion.** The first paragraph of your report must be:
> **Conclusion**: [ship/iterate/kill] [treatment name]. [one-sentence key finding].
> [2-3 key metric changes with numbers]. [specific recommendation].

Then provide the supporting analysis in the step order above.
Every data table MUST be followed by an interpretation paragraph.

## Statistical Standards
- z-test for proportions (retention, conversion). t-test for continuous metrics.
- Multiple comparisons: Bonferroni correction (alpha / N comparisons).
- Effect size: report absolute difference (pp) AND relative difference (%).
- MDE: calculate based on sample size. If sample < 500 per cell, mark "insufficient power."
- Confidence intervals: 95%, always reported alongside p-values.

## Iron Disciplines (non-negotiable)
1. SRM check is mandatory.
2. Population profile + baseline expectations are mandatory (Step 2).
3. Every metric comparison needs a z-test. Don't say "better" — say "z=2.31, p<0.05."
4. Deviations from baseline must be explained with population data (not dismissed).
5. Multiple comparisons: Bonferroni correction.
6. Sample < 500 per cell: do not conclude, mark "insufficient power."
7. Conclusions must be **causal sentences**: not "metric changed" but
   "[behavior] changed causing [metric] to change."
8. Dimension drill-downs MUST show data tables BEFORE conclusions.
9. **Show ALL groups** in every comparison — not just the winner.
10. **Behavioral verification is mandatory.** Without it, the analysis is incomplete.
"""

EXPLORATORY_PLAYBOOK_CONTENT = """\
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
"""


# ===========================================================================
# Factory: create registry with built-in playbooks
# ===========================================================================

def create_default_playbook_registry(
    playbook_dir: str | None = None,
) -> PlaybookRegistry:
    """Create a PlaybookRegistry pre-loaded with built-in playbooks.

    If *playbook_dir* is provided and exists, any .md files in that directory
    will also be loaded (and can override built-ins by name).
    """
    registry = PlaybookRegistry()

    # Register built-in playbooks
    registry.register(Playbook(
        name="diagnostic",
        description="Metric anomaly investigation — structured root cause analysis.",
        triggers=[
            "anomaly", "drop", "spike", "decline", "increase",
            "abnormal", "diagnose", "diagnostic", "root cause",
            "why did", "what happened", "what caused",
        ],
        content=DIAGNOSTIC_PLAYBOOK_CONTENT,
    ))

    registry.register(Playbook(
        name="ab_experiment",
        description="AB test analysis — 7-step statistical framework.",
        triggers=[
            "ab test", "a/b test", "experiment", "treatment",
            "control group", "variant", "ab analysis", "split test",
            "randomized", "experiment result",
        ],
        content=AB_EXPERIMENT_PLAYBOOK_CONTENT,
    ))

    registry.register(Playbook(
        name="exploratory",
        description="Open-ended exploratory analysis — lightweight discovery framework.",
        triggers=[
            "explore", "exploratory", "investigate", "look into",
            "tell me about", "what is", "how does", "overview",
            "understand", "deep dive",
        ],
        content=EXPLORATORY_PLAYBOOK_CONTENT,
    ))

    # Load user-supplied playbooks (may override built-ins)
    if playbook_dir:
        registry.load_directory(playbook_dir)

    return registry
