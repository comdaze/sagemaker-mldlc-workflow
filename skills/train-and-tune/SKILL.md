---
name: train-and-tune
description: Establishes naive baselines, trains a model against them, and tunes on validation data only - covering task-appropriate baselines computed before any model exists, the quality bound derived from them and declared before results, asserting the training input's identity, recording the model artefact and image by digest, and a search whose candidates are declared in advance and never see the test set. Use when training or retraining a model, choosing hyperparameters, deciding what a good result would be, or reviewing whether a reported score means anything. Applies to tabular, time-series and deep-learning work in every partition.
---

# Train and tune

Three stages that only make sense in this order. **Baselines first, because a model
cannot be judged before something exists to judge it against.** A run that trains first
treats whatever number it gets as the result — and the number will look reasonable,
because there is nothing to compare it to.

`data-pipeline` produced the inputs and their digests. `evaluate-and-gate` scores the
winner once, at the end. This skill covers everything between: establishing what good
would be, producing a candidate, and improving it without touching the held-out set.

**This body decides; the references measure.** Which baselines are appropriate and what a
search space looks like differ per task, and that is in `references/`.

## Stage 6: baselines, and the bound they imply

### Compute them before any model exists

A naive baseline is a prediction rule with no learned parameters. Every task has at least
two, they cost a single pass over the data, and they are the only thing that makes a
model's score interpretable.

**The refusal: do not proceed to training until the baselines are recorded.** Not as
advice — as a check that the training stage reads and fails on.

The reason, from a validation run of this power's predecessor: the pipeline reported MAE
18.8 against a quality gate of 130 and every structural check passed. One leaking input,
used directly as the prediction with no model at all, scored 14.2 — better than the
trained model. The naive baselines were 182.7 and 137.1. **Had those two numbers been
required outputs, 18.8 would have been suspect on sight** rather than six months later.

### The quality bound is derived here, not at the gate

This is the stage that decides what "good enough" means, and it must happen **now** —
while the model's score does not yet exist.

Write `contracts/quality-gate-contract.json` at this stage, with the bound derived from
the strongest baseline and the margin stated:

```
bound = strongest valid baseline, improved by the declared margin
```

Why here and not at stage 10: a real run got the substance right and still could not
show it. Its threshold was genuinely fixed 36 minutes before evaluation — but the file
named `quality-gate-contract.json` was written two minutes *after* the evaluation report,
so an auditor opening the gate contract saw a bound younger than the result it judged,
with the exonerating evidence in a different file they had no reason to open.

So: the contract file is written at stage 6, and the gate report at stage 10 cites its
digest and asserts that the contract predates the predictions. **"I did not peek" is not
evidence; a file that provably existed first is.**

### Name the strongest baseline, not the most flattering one

The bound comes from the **strongest** baseline that is valid at prediction time, which is
often much harder to beat than the mean. A model that beats the mean and loses to the
obvious domain heuristic has not earned a release, and choosing the weaker comparison is
the most common way a gate gets quietly set to something a model can clear.

If the strongest baseline is itself suspiciously good, that is `leakage-guard`'s screen
firing late — a "baseline" that is nearly the target is a leak, not a baseline.

## Stage 7: training

### Assert what you are training on

The processing report recorded a digest per output. Read it and assert the training input
matches before the job starts. **Refuse to train on an input whose digest does not match
the report**, because a training job takes a path, and a path is not an identity.

A test channel does not exist in a training job. If the code has one, that is a defect
regardless of whether it is currently pointed anywhere.

### Record the run so it can be found again

Three identities, all by digest and none by tag:

| Record | Why not the friendly form |
|---|---|
| the image, by digest | a tag moves, and then the run cannot be rebuilt |
| the model artefact, with its object version | a path can be overwritten |
| the input, by digest | see above |

Plus the resolved hyperparameters as they were actually sent, not as they were intended.
A default that changed between SDK versions is invisible in intent and visible in the
resolved set.

### A job that exited zero has not necessarily succeeded

Success is exit code **and** artefact. A job that finishes cleanly and writes no model is
a failure that reports as a success, and it is the shape that wastes the most time
downstream. `release-and-serve` makes this a gate; here it is the thing to assert before
recording the run as done.

Record billable time and instance type beside the result. Not for accounting — so that
"train a bigger one" is a decision with a number attached.

## Stage 8: tuning

### The test set is not a channel here

**The refusal: a tuning job with a test channel is refused, not warned about.** Tuning
selects among candidates by comparing scores; a comparison that can see the held-out set
consumes it, one experiment at a time, and every individual run still looks honest.

Validation is what tuning may read. Test is read once, by `evaluate-and-gate`, after the
winner is fixed.

### Declare the candidates before running them

Write the search space — or the explicit candidate list — into the contract first. Two
things follow that do not otherwise:

**The count is recorded**, which is what makes the winner's score interpretable. Picking
the best of six candidates on validation earns a smaller claim than picking the best of
one, and a reader who does not know the count cannot make that adjustment.

**Stopping is a rule rather than a feeling.** A declared budget — candidate count, wall
clock, or cost — ends the search at a point chosen before anyone had a favourite.

### The winner's test score is not yet known

Fix the winner on validation, then hand it to `evaluate-and-gate`. Do not look at the
test score to choose between candidates and then report that score as the evaluation;
that is the same set consumed twice, and it is indistinguishable in the artefacts from an
honest single evaluation unless the order was recorded.

A run did this correctly and recorded it: the tuned winner was fixed **before** the test
period was accessed. That ordering is the claim, so write it down.

## What goes in the contract

```yaml
spec:
  baselines:
    computed: [<name and score per baseline>]
    strongest: <name>
  quality:
    metric: <the metric the gate is denominated in>
    bound: <derived from the strongest baseline>
    marginPct: <the declared improvement required>
    contract: contracts/quality-gate-contract.json   # written at stage 6
  training:
    inputDigests: {<channel>: <digest from the processing report>}
    imageDigest: <sha256:...>
    resolvedHyperparameters: {<as sent, not as intended>}
  tuning:
    candidates: [<declared before running>]
    budget: <count | wallClock | cost>
    selectOn: validation
    winnerFixedBeforeTestAccess: true
```

## Check it mechanically

```bash
CHK=$(ls ~/.kiro/powers/installed/*/skills/train-and-tune/scripts/training-check.py \
      ./skills/train-and-tune/scripts/training-check.py 2>/dev/null | head -1)
python3 "$CHK" artifacts/baseline-report.json artifacts/training-report.json \
               artifacts/tuning-report.json
```

It refuses on: no baselines recorded, a bound not derived from the strongest baseline, a
bound weaker than a baseline it claims to improve on, an image or input recorded by tag
instead of digest, a training run with no model artefact, a tuning run carrying a test
channel, a candidate count that disagrees with the declared list, and a winner whose
selection is not recorded as preceding test access.

## Prove the refusals fire

Break each record one field at a time and assert non-zero exit. The test-channel refusal
matters most: add a `test` channel to a tuning record and confirm it is refused, because
that is the one whose absence cannot be noticed from the outputs.

## References

- `references/baselines-and-search.md` — which baselines are appropriate per task, and
  what a search space looks like when the model is a tree, a linear model or a network.
- `leakage-guard` — a baseline that is nearly the target is a leak.
- `evaluate-and-gate` — reads the bound this stage declared, once.
- `runtime-and-containers` — what runs the job, and how the image is resolved.
