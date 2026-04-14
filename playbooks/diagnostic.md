---
name: diagnostic
description: Metric anomaly investigation with structured root cause analysis
triggers: [anomaly, drop, spike, decline, "why did", "what happened", "what caused"]
---

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
