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

You are investigating a metric anomaly. Follow this structured framework to
identify the root cause with quantitative rigor.

## Step 1: Confirm the Anomaly
- Query the metric's recent trend (7-day and 30-day windows).
- Determine: is this a real anomaly or normal variance?
- Quantify the deviation: how many standard deviations from the moving average?
- Note the exact timeframe when the anomaly started and whether it has recovered.

## Step 2: Dimension Decomposition
- Break the metric down by its key dimensions (e.g. platform, country, channel,
  user segment, product area).
- For each dimension, calculate the **contribution** to the total change:
  `contribution = (segment_delta / total_delta) * 100%`
- Identify which segments are driving the anomaly vs. which are stable.
- **Discipline**: always quantify contribution percentages. Do not stop at
  "segment X went down" — state "segment X contributed 73% of the total decline."

## Step 3: Distinguish Volume vs. Rate Changes
- For ratio metrics (e.g. conversion rate), always decompose into:
  - Numerator change (volume effect)
  - Denominator change (mix effect)
  - True rate change (behavioral effect)
- A "rate drop" caused entirely by denominator inflation is a different root cause
  than a genuine behavioral shift.

## Step 4: Root Cause Investigation
- Based on the dimension decomposition, drill into the top contributing segments.
- Look for coinciding events: deployments, experiments, marketing campaigns,
  external factors (holidays, outages, competitor actions).
- Cross-reference with related metrics to build a causal narrative.
- **Discipline**: do not stop at the dimension level. "Mobile dropped" is not a
  root cause — investigate *why* mobile dropped.

## Step 5: Cross-Validation
- Validate the hypothesis by checking:
  - Does the timing align precisely with the suspected cause?
  - Are unaffected segments truly stable (control group logic)?
  - Does a complementary metric confirm the story?
- If the hypothesis does not hold under cross-validation, return to Step 2.

## Step 6: Summary and Recommendations
- State the root cause clearly in one sentence.
- Quantify the impact (magnitude and duration).
- Provide actionable recommendations ranked by expected impact.
- Flag any data quality concerns or open questions.

## Disciplines (apply throughout)
- Always start broad, then narrow. Do not jump to a hypothesis before decomposition.
- Every claim must have a number attached.
- Prefer absolute numbers alongside percentages for context.
- If data is insufficient to reach a conclusion, say so explicitly — do not speculate.
"""

AB_EXPERIMENT_PLAYBOOK_CONTENT = """\
# AB Experiment Analysis Playbook

You are analyzing an AB experiment. Follow this 7-step framework to produce
a rigorous, actionable analysis.

## Step 1: Sample Ratio Mismatch (SRM) Check
- Compare actual group sizes to expected allocation ratios.
- Use a chi-squared test: if p < 0.01, flag SRM and **stop the analysis**.
- SRM invalidates all downstream results. Investigate the assignment mechanism
  before proceeding.

## Step 2: Baseline Validation
- Confirm pre-experiment parity between treatment and control on key metrics.
- Check that randomization produced balanced groups on observable covariates.
- If significant pre-experiment differences exist, note them and consider
  adjustment methods (CUPED, stratified analysis).

## Step 3: Causal Hypothesis
- State clearly: "We hypothesize that [treatment] causes [expected effect]
  on [primary metric] because [mechanism]."
- Define the causal chain: treatment -> intermediate behavior -> outcome metric.
- This chain will be validated in Step 6.

## Step 4: Primary and Secondary Metrics
- **Primary metric**: calculate the difference, confidence interval, and p-value.
  - Use a two-sided z-test for proportions or t-test for means.
  - Report the observed lift: `(treatment - control) / control * 100%`
  - Report 95% confidence interval for the lift.
  - Compare observed effect to Minimum Detectable Effect (MDE).
- **Secondary metrics**: apply Bonferroni correction for multiple comparisons.
  - With N secondary metrics, use significance threshold `alpha / N`.
- **Guardrail metrics**: check that no guardrail metric has degraded beyond
  the acceptable threshold.

## Step 5: Dimension Drill-Down
- Break down the treatment effect by key dimensions (platform, country,
  user segment, new vs. returning, etc.).
- Look for heterogeneous treatment effects: does the treatment help one
  segment while hurting another?
- **Discipline**: dimension drill-downs are exploratory. Clearly label any
  subgroup finding as hypothesis-generating, not confirmatory.

## Step 6: Behavioral Verification
- Trace the causal chain from Step 3:
  - Did the intermediate behavior change as expected?
  - Is the magnitude of intermediate change consistent with the outcome change?
- Example: if the hypothesis is "new onboarding flow increases retention because
  users complete more steps," verify that step completion actually increased.
- If the causal chain breaks, the result may be spurious or driven by a
  different mechanism.

## Step 7: Conclusion and Cross-Validation
- Summarize: ship, iterate, or kill.
- State confidence level and key caveats.
- Cross-validate: does the result make sense given prior experiments,
  domain knowledge, and related metric movements?
- Provide specific next-step recommendations.

## Statistical Standards
- Default significance level: alpha = 0.05 (two-sided).
- Always report confidence intervals, not just p-values.
- For ratio metrics, use delta method or bootstrap for variance estimation.
- For sequential testing, use appropriate group sequential boundaries.
- Minimum experiment duration: 7 days (to capture day-of-week effects).
- Document any deviations from the pre-registered analysis plan.

## Template Placeholders
- Experiment assignment table: `{{experiment_table}}`
- Experiment variant column: `{{variant_column}}`
- User identifier column: `{{user_id_column}}`
- Experiment name/ID filter: `{{experiment_filter}}`
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
