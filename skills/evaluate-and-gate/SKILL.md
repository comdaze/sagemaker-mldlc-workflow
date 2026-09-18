---
name: evaluate-and-gate
description: Scores a fixed model once on the held-out set and decides whether it may be registered - covering independent recomputation of every reported metric, keyed predictions emitted as an artefact, the model's score reported beside its baselines, declared metrics that were not implemented recorded rather than dropped, and a quality gate that fails closed and carries the command that clears it. Use when evaluating a trained or tuned model, setting or applying a release threshold, deciding whether a result is good enough to ship, or reviewing why a model was or was not registered. Applies to tabular, time-series and deep-learning work in every partition.
---

# Evaluate and gate

Two stages and one irreversible act. **The held-out set is consumed by being read**, so
stage 9 happens once, on a model that was already fixed, and stage 10 decides what may
happen next.

`train-and-tune` fixed the winner and declared the bound at stage 6, before any score
existed. This skill reads that bound and applies it. It does not set it — a gate that
chooses its own threshold after seeing the result is not a gate.

**This body decides; the references measure.** Which metrics are appropriate, and what
counts as primary versus diagnostic, differ per task and are in `references/`.

## Stage 9: evaluate once

### The winner must already be fixed

Before reading the test set, assert that `tuning.winnerFixedBeforeTestAccess` is recorded
as true and that the model identity being evaluated matches the one tuning selected.
**Refuse to evaluate a model that was not fixed first.** This is the one property that
cannot be recovered afterwards: once several candidates have been compared on the test
set, no later analysis distinguishes that from an honest single evaluation.

### Recompute every number you report

Do not copy the metric the training or tuning job printed. Emit predictions, then compute
the metrics from the predictions yourself, and record both — the job's number and yours.

They should agree. When they do not, the disagreement is the finding: a different metric
implementation, a different aggregation, a different subset. A run that recomputed 30
metrics independently and confirmed each is the reason its numbers can be quoted at all;
a number nobody recomputed is a number one implementation deep.

### Emit keyed predictions, not just a score

The artefact of this stage is a **prediction per sample, keyed**, plus the metrics derived
from it. A summary metric alone is a dead end: the gate can use it, and nothing else can.
Keyed predictions let the gate recompute, let error be sliced afterwards, and give
`monitor-and-retrain` the record it needs to compare live error against this same bound.

### Report the ratio, not the score alone

Publish the model's score **beside the baselines from stage 6 and the best
single-input-as-prediction score** from `leakage-guard`'s screen. Three numbers, always
together.

A model that beats its bound by an order of magnitude has usually found a leak rather
than a signal, and the ratio makes that visible on sight. A model that beats the mean and
loses to the domain heuristic has not earned a release, and only the comparison says so.

### A declared metric that was not implemented is recorded, not dropped

**The refusal: refuse to emit an evaluation report whose metric list is shorter than the
contract's without `metrics.omitted[].reason` explaining each absence.**

This exists because of a specific failure in an audited run: a metric from the declared
list went unimplemented, the report looked complete, and nothing caught it. An omission
that is recorded is a decision; an omission that is silent is indistinguishable from a
metric that passed.

## Stage 10: the gate

### It fails closed

On a fail, nothing is registered, nothing is released, and no downstream stage proceeds on
the assumption that it might have passed. The default is refusal; passing is what requires
evidence.

### It reads a bound it did not set

The gate cites the contract's digest and **asserts that the contract file predates the
predictions**. That assertion is the whole reason the bound is written at stage 6, and it
is checkable in a way that "I did not peek" is not.

**Refuse to apply a gate whose contract cannot be shown to predate the result.** Not
because anyone is assumed dishonest, but because an artefact that cannot demonstrate the
ordering has not recorded the thing that makes it meaningful.

### Diagnostic metrics inform; they cannot override

One metric is primary and it decides. Everything else — per-slice error, tail behaviour,
calibration, a metric on a subpopulation — is diagnostic: it explains a verdict and may
justify a new contract, but it cannot turn a failed primary into a pass.

A gate with several equal metrics is not a gate; it is a negotiation, and the negotiation
happens after the numbers are known.

### The refusal carries the command that clears it

A refusal that only says no leaves the user to work out what to do. State what was
declared, what was found, and the shortest path from one to the other — a different
model, a revised contract with the change named, or more data. `ml-planning`'s rule
applies here in full: a refusal may stop the work, but it may not quietly change what the
work was.

### A waived gate is not a passed gate

If the user chooses to proceed past a refusal, the gate verdict stays REFUSED. Record the
task as `[!]` in `PLAN.md` with `refused:` pointing at the gate report and `blocks:`
naming what remains blocked — `plan-lint.py` then fails if any blocked task is marked
done. A real run did exactly this: the gate stayed REFUSED, the compiled pipeline
contained no registration step, and the downstream tasks stayed blocked.

## What goes in the contract

```yaml
spec:
  evaluation:
    metrics:
      primary: <the one that decides>
      diagnostic: [<the ones that explain>]
    predictionsArtefact: artifacts/test-predictions.csv
    recomputed: true
  quality:
    contract: contracts/quality-gate-contract.json   # written at stage 6
    bound: <from train-and-tune, not set here>
```

## Check it mechanically — and let the script decide

```bash
GATE=$(ls ~/.kiro/powers/installed/*/skills/evaluate-and-gate/scripts/quality-gate.py \
       ./skills/evaluate-and-gate/scripts/quality-gate.py 2>/dev/null | head -1)
python3 "$GATE" contracts/quality-gate-contract.json artifacts/evaluation-report.json \
        --out artifacts/quality-gate-report.json
```

**The script computes the verdict; it does not check a verdict someone else wrote.** That
distinction is the point. It reads the bound and the measured score, derives PASS or
REFUSED itself, writes the report, and exits non-zero on REFUSED — so a failing gate stops
a pipeline step rather than producing a document someone has to read.

It refuses on: **a bound that does not follow from the artefact its own `derivedFrom`
names**, a contract that cannot be shown to predate the predictions, a model worse
than any recorded baseline, a metric list shorter than the contract's without recorded
reasons, a primary metric absent from the evaluation, an evaluation that does not record
independent recomputation, and a recorded verdict that disagrees with the computed one.

### The bound has to be reproducible, because `declaredAt` is a field its author can rewrite

Declare where the bound came from, not only when:

```json
"bound": 48.583,
"derivedFrom": {"artifact": "artifacts/baseline-report.json",
                "baseline": "day-ahead price", "marginPct": 5}
```

The gate redoes the arithmetic. A validation run rewrote its contract and left the old
`declaredAt` in place — which would have forged the very evidence the timestamp exists to
provide, and nothing in the script could have told. Widening a bound after seeing the score
now breaks a calculation, and it breaks it whatever the timestamp says. The limit is worth
knowing: rewriting the baseline report too would defeat this, so the two artefacts together
are harder to fake than either alone — harder, not impossible.

### Inside a Pipeline, route on the report rather than on a crash

Exiting non-zero is what makes the refusal hold in a shell, so it is the default. But a
metric missing its bound is a **result**, not a crashed job: a `ConditionStep` should read
`registrationAllowed` and branch. Pass `--no-fail-on-refusal` for that, and the report
records `failOnRefusalSuppressed: true` — without the trace the flag would quietly convert a
refusal into advice. A validation run wrote its own 110-line wrapper to get this behaviour,
and the wrapper was right.

## Prove the refusals fire

The gate is the constraint this whole power is organised around, so its test is not
optional. Run the script against an evaluation that fails the bound and assert exit
non-zero and `registrationAllowed: false`; then against one that passes and assert exit
zero. A gate that has only ever been run on passing results is a gate nobody has seen
work.

## References

- `references/metrics-by-task.md` — which metric is primary per task, which are
  diagnostic, and the threshold semantics for each.
- `train-and-tune` — fixed the winner and declared the bound.
- `release-and-serve` — reads this verdict, and registers nothing without it.
- `monitor-and-retrain` — compares live error against this same bound.
