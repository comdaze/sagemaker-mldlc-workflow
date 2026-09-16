---
name: leakage-guard
description: Decides which features a supervised model may use, by asking whether each value was knowable at prediction time - covering point-in-time correctness, split hygiene, and a behavioural screen that refuses to train on a feature that nearly is the target. Use before training any model, when choosing or reviewing a feature set, when an offline score looks too good, or when deciding train/validation/test splits. Applies to tabular, time-series and deep-learning work in every partition.
---

# Leakage guard

One question decides admissibility:

> **Was this value knowable at prediction time?**

Everything below is machinery for answering it honestly, because the two ways of
getting it wrong both look like success. Refuse too much and you ship a model that
can only see the calendar. Refuse too little and you ship an offline score that
production will not reproduce — and that failure is the expensive one, because
every structural check passes on the way out.

## Step 1: name the prediction time

"Prediction time" is domain-defined, and it is a decision, not a discovery. Write it
in the contract (`spec.data.predictionTime`) before selecting a single feature.

| Domain | Prediction time is | So this is inadmissible |
|---|---|---|
| Time-series / market forecast | the forecast cut-off — a wall-clock instant relative to each target period | any value published after the cut-off for that period |
| Credit / underwriting | the moment the application is submitted | anything from the servicing history that follows |
| Churn / retention | the end of the observation window | anything from the label window itself |
| Fraud / abuse | the moment the transaction is scored | the adjudication outcome, and anything derived from it |
| Predictive maintenance | the moment the alert would fire | readings after the failure, and the repair record |

Two consequences worth stating in the plan rather than discovering later. A single
model may have **one prediction time per prediction**, so a feature admissible for
a 24-hour-ahead forecast may be inadmissible for a 1-hour-ahead one — if both are
wanted, they are two contracts. And prediction time is not the same as *training*
time: the training set is assembled long after the fact, which is exactly why the
data has values that were not available then.

## Step 2: sort every candidate into three tiers

Only the third is a refusal. Sorting is a hypothesis to be tested in step 3, not a
conclusion.

| Tier | What it is | Verdict |
|---|---|---|
| **Structurally impossible to be late** | derived from the calendar or the request itself — hour, weekday, holiday flag, the submitted application's own fields | Allowed, no assumption needed |
| **Available by design, unverified here** | a forecast published by an upstream operator before the cut-off; a third-party score refreshed on a known schedule; a contract term agreed in advance | **Allowed**, with the assumption recorded in the plan |
| **Known to be after the fact** | actuals, realized values, settlement and adjudication outcomes, final statuses, resolution codes, post-hoc labels, and any lag or rolling statistic computed from them | **Forbidden**, and the validator must reject it |

**The middle tier is where the damage happens, in both directions.**

Forbidding it is the responsible-looking failure: a plan that withholds every
feature whose publication time it cannot prove ends up with a calendar-only model.
Hour-of-day cannot forecast a price or score a loan. That is not a safe answer, it
is a useless one, and it is discovered late because the pipeline runs perfectly.

So the default for tier 2 is **allow and record**. A value named for a product that
exists before the event is published before the event — that is what the product
*is*. Reserve refusal for what you have positive reason to believe is late.

Anything in tier 3 that is genuinely needed becomes a **new feature at a later
prediction time**, not an exception to the rule.

## Step 3: screen every feature against the target — this is a refusal

Sorting by name is a hypothesis, and **names lie**. Run a screen that costs one
pass over the data, and treat it as a gate rather than a diagnostic.

The measured case this exists for: a column named as a *long-term reference price*
sounded contracted in advance and was allowed into tier 2. Used **directly as the
prediction, with no model at all**, it scored MAE 14.18 against a target whose
standard deviation was 232, correlating 0.975 — better than the trained model that
had been permitted to use it. It was a settlement quantity derived from realized
outcomes. Every structural check passed. The quality gate passed. The result was
worthless.

```
naive: predict the mean            MAE 182.71
naive: same period yesterday       MAE 137.06
the suspect column, used as-is     MAE  14.18   corr 0.975   ← the leak
the trained model                  MAE  18.76   ← worse than the leak alone
```

### What to compute

1. For each candidate feature, its correlation with the target — and where the
   units are comparable, its error **used directly as the prediction**.
2. For a classification target, the same idea with a classification measure: AUC of
   the single feature, and its mutual information with the label.
3. Two naive baselines beside them: predict the mean or majority class, and predict
   the previous period's value at the same position.

### What refuses

- **A correlation above a declared bound** — `spec.data.leakageScreen.maxAbsCorrelation`,
  0.95 is a reasonable default. Failing the screen is a hard stop, and the bound
  lives in the contract so raising it is a reviewable act rather than an edit
  nobody sees.
- **A single feature whose direct-as-prediction error is anywhere near the quality
  gate.** If one column already nearly clears the bar the model must clear, the
  model is not the thing being measured.
- **A single-feature AUC above roughly 0.95** on a classification target, for the
  same reason.

A feature that nearly *is* the target is either the target under another name or
computed from it. There is no third explanation worth training on, and no naming
convention will surface this — only the comparison will.

### Prove the refusal fires, on data you control

A screen that has never refused anything is indistinguishable from a screen with a
sign error, and both report PASS. So every run must include one **probe**: take a
column you know is inadmissible, put it through the same code path, and record that
it was refused.

A real run did this without being asked, and it is the strongest thing in that run's
evidence. Its leakage audit recorded eight checks as PASS — and separately, an
in-memory node-price probe that **triggered both statistical refusal checks**. The
first half says the admissible features passed. Only the second half says the checks
work.

Build the probe from data you already have rather than fabricating one. The target
itself, lightly perturbed, is the cheapest inadmissible column that exists:

```python
probe = y + rng.normal(0, y.std() * 0.01, len(y))   # correlation ~0.9999
assert screen(probe) is REFUSED, "the leakage screen did not fire on the target itself"
```

Record the probe's own numbers in the audit artefact — correlation, direct-prediction
error, which bound it crossed — not just that it was refused. `README.md` classifies
this power's constraints by whether "a test can prove the refusal fires". This is that
test, and until a run carries it the classification is a claim about the code rather
than a measurement of it.

**Never run the probe against the real feature set and keep going.** Its purpose is to
verify the screen, so it lives in memory, never reaches a training channel, and never
appears in the feature policy.

### Report the ratio, not just the score

Publish the model's error **beside the best single-feature-as-prediction score and
the naive baselines**, on the release page and in the training report. A model that
beats its own quality gate by an order of magnitude has usually found a leak rather
than a signal, and that ratio makes it visible immediately instead of after
deployment. `baseline-first` is the stage that makes this unavoidable.

## Step 4: split hygiene — the other family

Point-in-time correctness is about *which columns*. This is about *which rows*, and
it leaks just as thoroughly with a perfectly admissible feature set.

| Failure | What it looks like | The rule |
|---|---|---|
| **Fit before split** | a scaler, imputer, encoder or feature-selector fitted on all rows, then applied per fold | fit on the training partition only, inside the fold |
| **Target encoding on full data** | a category replaced by its mean target, computed over every row | compute out-of-fold, or from the training partition alone |
| **Group bleed** | the same customer, device, site or patient in both train and test | split by group, never by row |
| **Temporal bleed** | a random shuffle on time-ordered data | split chronologically; validation must be strictly later than training |
| **Duplicate rows** | the same record present in both partitions | de-duplicate before splitting, on a declared key |
| **Repeated tuning on the test set** | the held-out set consulted once per experiment | tune on validation; touch test once, at the end |

Two of these have a mechanical check worth writing, so they belong in the pipeline
rather than in a reviewer's memory: **assert the intersection of group keys across
partitions is empty**, and **assert max(train timestamp) < min(validation
timestamp)**. Both are one line and both fail loudly.

The preprocessing rule has a structural form too: put every fitted transform inside
the pipeline step that also does the split, so there is no code path in which a
transform can see rows it should not. A transform fitted in a notebook cell above
the split is the classic shape of this bug.

## Step 5: what goes in the contract

```yaml
spec:
  data:
    predictionTime: <the instant, and what it is relative to>
    leakageScreen:
      maxAbsCorrelation: 0.95
      maxSingleFeatureAuc: 0.95        # classification targets
    features:
      allowed: [<explicit list — never "everything else">]
      forbidden: [<tier 3, by name and by pattern>]
      assumed:                          # tier 2
        - feature: <name>
          reason: <why it is believed available at prediction time>
    split:
      strategy: chronological | grouped | stratified
      groupKey: <column, when grouped>
      boundaries: <dates or fractions>
```

`features.allowed` is an explicit allowlist rather than an exclusion rule for the
same reason it always is: when a new column appears upstream, an allowlist keeps
the model unchanged until someone decides, while "everything except" silently
absorbs it — including if it is an actual.

`forbidden` should carry patterns as well as names (`*_actual`, `settle*`,
`*_final`, `resolution_*`), because the next column to leak has not been named yet.

## Step 6: record what you could not verify

Tier 2 rests on an assumption. Say so, in the plan, once per assumption:

```markdown
## Constraints traded away

- Upstream publication punctuality is assumed, not verified: no per-period
  publication-time record exists for the day-ahead sources. The pipeline enforces
  the cut-off structurally, so a late value cannot enter a feature; what is
  unverified is whether the source was ever late in the history we trained on.
```

**Do not demand a publication-proof record as a precondition for registering the
dataset.** A per-period, per-source `published_at` table exists only where an
upstream platform already produces one. Asking for it blocks the entire plan on an
artefact nobody has. If the user happens to have one, use it to *check* the
assumption later; its absence is a recorded gap, not a stop.

## Two traps where the column name looks innocent

- **A settled or resolved value is not a prediction.** Anything whose name involves
  settlement, clearing, adjudication, reconciliation or a final status is computed
  from the outcome by definition. Check the process timetable rather than the word:
  a value described as belonging to the day *before* the target may still be
  produced after a morning cut-off.
- **An observation is not a forecast.** Weather, sensor and telemetry columns pulled
  from a historical archive are usually reanalysis or actuals — what *happened*.
  Using them as features for the target period is leakage that no metadata can fix,
  because the value never existed at the prediction time. The remedy is a different
  data pull — what the forecast said at that past moment — not a publication-time
  record.

When you cannot yet tell which tier a feature belongs to, **say which check would
decide it** and put that check in the plan as a task: "is this weather column
reanalysis, or a forecast issued before the cut-off?" A named, answerable check
beats both a deadlock and a silent exclusion.

## What this skill does not cover

Adversarial or intentional label leakage, and privacy-motivated feature exclusion.
Both are real concerns with different machinery; nothing here addresses them, and
saying so is better than implying coverage.
