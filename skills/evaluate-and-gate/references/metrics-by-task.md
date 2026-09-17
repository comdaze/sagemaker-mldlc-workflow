# Metrics by task

Contents:
- Which metric is primary, per task
- Which are diagnostic, and what they are for
- Threshold semantics: direction, and what a margin means
- Three metric traps that pass every structural check

`SKILL.md` says one metric is primary and decides, the rest explain, and a declared
metric that was not implemented is recorded rather than dropped. This says which is
which.

## Choosing the primary metric

The primary metric is the one the bound is denominated in. Pick it for the decision the
prediction feeds, not for familiarity.

### Regression and forecasting

| Metric | Lower better | Choose it when |
|---|---|---|
| MAE | yes | the cost of error is roughly linear; the default worth defending |
| RMSE | yes | large errors cost disproportionately more |
| MAPE / sMAPE | yes | error matters relative to magnitude — but see the traps |
| Pinball loss | yes | the prediction is a quantile, not a point |
| MedAE | yes | the target has outliers you do not want dominating the bound |

MAE and RMSE disagree in a useful way. A model better on MAE and worse on RMSE is making
fewer small errors and more large ones — which is a real trade, and which one is
acceptable is a decision for the consumer of the prediction, not for whoever is training.

### Classification

| Metric | Higher better | Choose it when |
|---|---|---|
| PR-AUC / average precision | yes | positives are rare and you care about finding them |
| ROC-AUC | yes | classes are roughly balanced and ranking is the product |
| F1 at a declared threshold | yes | a single operating point is what ships |
| Recall at fixed precision | yes | a business rule fixes one side |
| Log loss / Brier | **no** | the probability itself is consumed, not a label |

**Accuracy is almost never the primary metric.** On an imbalanced problem the majority-
class baseline already scores well, so a bound denominated in accuracy is cleared by a
model that predicts one class forever. If accuracy is what a stakeholder asks for, report
it as diagnostic and make the bound something that can fail.

### Unstructured deep learning

Same metrics as the task underneath — a classifier is a classifier — with two additions:

| Metric | Note |
|---|---|
| Top-k accuracy | when several labels are plausible and a shortlist is the product |
| Per-class recall, worst class | a strong overall number over a corpus with a rare class routinely hides a class the model never predicts |

The second is worth promoting to primary when any class matters individually. An overall
metric on 40 classes can be excellent while one class is at zero.

## Diagnostic metrics, and what they are for

They explain a verdict and can justify a *new* contract. They cannot override a failed
primary — see `SKILL.md`.

| Diagnostic | Answers |
|---|---|
| Error by slice | is the model bad everywhere, or bad in one segment |
| Tail error (P90, P95 of per-unit error) | how bad is the worst day, customer, or batch |
| Calibration | when a probability is consumed as a probability |
| Error by prediction magnitude | is the model bad only where the target is extreme |
| Confusion between specific classes | which pairs it cannot tell apart |

A worthwhile pattern: report the primary metric per prediction unit as well as pooled, and
publish a tail percentile of that distribution. A model with an acceptable mean and an
unacceptable worst case is a model whose failures are concentrated, and the pooled number
alone cannot show it.

## Threshold semantics

Three things the contract must state, because none is inferable from the number:

**Direction.** Whether smaller or larger is better. A bound of `0.85` is meaningless
without it, and getting it backwards produces a gate that passes exactly the models it
should refuse.

**Inclusivity.** Whether the bound itself passes. `<=` versus `<` matters when the bound
is derived from a baseline and the model ties it.

**What the margin is a margin over.** A declared improvement of 5% over a baseline of
51.1 is 48.5 — and 5% of *what* has to be stated, because a percentage of the baseline
and a percentage of the target's range are different numbers.

## Three metric traps that pass every structural check

**MAPE explodes near zero and is asymmetric.** With targets crossing or approaching zero,
a single small-denominator sample dominates the aggregate, and the metric penalises
over-prediction differently from under-prediction. If the target can be near zero, MAPE is
not the primary metric however conventional it is in the domain.

**A pooled metric hides a per-unit failure.** Averaging over every sample lets a model
that is catastrophic on 5% of units look fine. Report per-unit and pooled, and put a tail
percentile in the diagnostics.

**A metric computed on a filtered subset is not the metric.** Dropping samples the model
could not score — missing inputs, out-of-range targets, unparseable items — and reporting
the metric on what remains produces a number that improves as the pipeline gets worse.
Record the count scored and the count dropped beside the metric, always. This is the same
reconciliation `data-pipeline` requires of its partitions, applied to results.
