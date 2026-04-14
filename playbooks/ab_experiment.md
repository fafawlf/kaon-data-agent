---
name: ab_experiment
description: AB test analysis — 7-step causal inference framework
triggers: ["ab test", experiment, treatment, control, variant, "split test"]
---

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
- Query the experiment population's characteristics and compare to your overall user base.
- This tells you WHO is in the experiment, which determines your expectations.
- **You MUST output baseline expectations**:
  > Based on population characteristics, expected D1 retention is approximately X%.
- **All subsequent comparisons use this baseline as anchor.** Deviations from baseline
  MUST be explained using population data, not dismissed as "different methodology."

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
2. Check transmission: do users with the behavior change also show the metric change?
3. Rule out alternatives: could another behavior explain the metric change?
4. Quantify: "[behavior X] changed by [Y%], explaining [Z%] of the metric change."
   If you can't quantify, mark "unable to isolate specific behavioral mechanism."

### 7. Cross-Validation + Conclusion
- Check for novelty effects: plot the treatment effect by day. Is it stable or decaying?
- Cross-validate with related metrics.
- Actively look for counter-evidence.

**Conclusion format**: ship / iterate / kill, with specific reasoning.

## Statistical Standards
- z-test for proportions (retention, conversion). t-test for continuous metrics.
- Multiple comparisons: Bonferroni correction (alpha / N comparisons).
- Effect size: report absolute difference (pp) AND relative difference (%).
- MDE: calculate based on sample size. If sample < 500 per cell, mark "insufficient power."
- Confidence intervals: 95%, always reported alongside p-values.

## Iron Disciplines (non-negotiable)
- SRM check is mandatory.
- Population profile + baseline expectations are mandatory.
- Every metric comparison needs a z-test. Don't say "better" — say "z=2.31, p<0.05."
- Dimension drill-downs MUST show data tables before conclusions.
- **Show ALL groups** in every comparison — not just the winner.
- **Behavioral verification is mandatory.** The analysis is incomplete without it.
- Conclusions must be causal: not "metric changed" but "[behavior] changed causing [metric] to change."
