# Screening scalar inputs

The statistics and bounds for part 3 when a candidate input is a single number per
sample — a table column, a computed aggregate, a third-party score. `SKILL.md` says
what a refusal is and that it does not proceed; this says what to measure.

For images, text, audio or embeddings, read `screening-unstructured.md` instead.
When the source has a time axis, read `temporal-sources.md` as well.

## What to compute

One pass over the training partition, for every candidate:

1. **Correlation with the target.** Absolute Pearson for a numeric target; Spearman
   as well when the relationship may be monotone but not linear.
2. **Error used directly as the prediction**, wherever the units are comparable —
   the candidate substituted for the model, scored with the same metric the quality
   gate uses. This is the check that catches a leak a correlation can miss, because
   it is denominated in the units the gate is denominated in.
3. **For a classification target**, the single-input AUC and its mutual information
   with the label.
4. **Naive baselines beside them**, so the numbers have a scale: predict the mean for
   a numeric target, the majority class for a categorical one. `baseline-first` owns
   the full baseline set; two are enough here to read the screen.

## The bounds

Declared in the contract, never hard-coded:

```yaml
spec:
  data:
    leakageScreen:
      maxAbsCorrelation: 0.95          # numeric target
      maxSingleFeatureAuc: 0.95        # classification target
      minDirectPredictionMarginPct: 20 # see below
```

`0.95` is a defensible default for both, and it is a default rather than a law. A
domain where two instruments measure the same physical quantity will legitimately
produce correlated inputs; raising the bound there is fine **as a contract edit**,
which is reviewable, and not as a code change nobody sees.

`minDirectPredictionMarginPct` is the third bound and the one most often missing: if
a single input used as the prediction lands within that margin of the quality gate,
refuse. If one column already nearly clears the bar the model must clear, the model
is not the thing being measured. Twenty percent is a starting point; the right value
follows from how tight the gate is.

## The probe

`SKILL.md` requires every run to prove its screen fires. For a numeric target the
cheapest inadmissible input is the target itself, perturbed:

```python
probe = y + rng.normal(0, y.std() * 0.01, len(y))   # correlation ~0.9999
assert screen(probe) is REFUSED, "the screen did not fire on the target itself"
```

For a classification target, a probe that is the label with a small fraction of
flips:

```python
flip = rng.random(len(y)) < 0.01
probe = np.where(flip, 1 - y, y)                     # AUC ~0.99
assert screen(probe) is REFUSED, "the screen did not fire on the label itself"
```

Record the probe's own numbers in the audit artefact — which statistic, what value,
which bound it crossed — not merely that it was refused. A recorded PASS on the real
inputs says they passed; only the probe says the checks work.

The probe lives in memory. It never reaches a training channel, never enters the
feature policy, and never appears in a processed dataset.

## Two shapes that defeat a correlation check

- **A non-monotone leak.** A value derived from the outcome through a non-monotone
  transform can have near-zero Pearson correlation and still determine the target.
  Mutual information catches these; correlation alone does not.
- **A leak that only exists in part of the data.** An input that is post-hoc for
  some rows and legitimate for others averages out to an unremarkable statistic.
  Screen within the partitions the split will produce, not only over the whole
  training set.
