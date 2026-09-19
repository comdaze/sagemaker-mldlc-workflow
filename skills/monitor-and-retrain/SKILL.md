---
name: monitor-and-retrain
description: Watches a deployed model and decides when to retrain it - what evidence to archive per prediction, how to detect drift and degradation without the managed capability, when accumulated error or new labelled data justifies a new run, and where that run re-enters the workflow. Also decides whether a missing or retired SageMaker capability should be self-built or read from an API you already have, covering experiment tracking, drift monitoring, dashboards, lineage and endpoint sizing. Use after a model is serving, when monitoring or drift comes up, when deciding to retrain, and BEFORE building any replacement for a managed capability that is absent, closed to new customers, or has no console page. Applies in every partition; the AWS China partition just forces the decision sooner.
---

# Don't rebuild what you can read

Every capability you self-build becomes yours to run forever. Read this before
building one.

The AWS China partition has no managed MLflow, no Inference Recommender and no
Studio pages for Experiments or Model Registry — and separately, ten SageMaker
features entered maintenance mode **globally** on 2026-07-30 and are closed to new
customers, Model Monitor among them. So this is not a China question. China just
gets you to it first.

## Stages 15–16 — the rule

> **Mirror invariants. Read mutable state live.**
>
> Treat the managed service as the *execution carrier*, not the *memory carrier*.

An invariant is a fact that can never change about a thing that already exists: a
model version's number, its artefact URI, the metrics it was gated on, the digests
it was built from. Mirror those into your own store freely — they cannot go stale.

Mutable state is anything that changes after the fact. `ModelApprovalStatus` is
the canonical example: **read it from SageMaker on every request.** A mirrored
approval status is a bug with a delay on it.

The payoff is durability. When a product is retired or frozen, you lose an
execution surface, which is replaceable. If your memory lived in it, you lose your
history, which is not.

## Stage 15 — two replacements that were built and then removed

Both were correct to build at the time and correct to remove. This is the honest
part, and it is the part no vendor material contains.

### Self-hosted MLflow → a stateless read layer

**Built:** MLflow on ECS Fargate, Aurora as backing store, S3 as artifact store,
behind an internal load balancer. A real service, with a database to upgrade,
patch and back up.

**Removed, and replaced with nothing that stores anything.** An experiment view
assembled on demand from APIs that already existed:

| View element | Source |
|---|---|
| Run | A training or processing job |
| Parameters | `DescribeTrainingJob.HyperParameters` / `DescribeProcessingJob.Environment` |
| Summary metrics | `DescribeTrainingJob.FinalMetricDataList` |
| Per-step metric curve | CloudWatch `GetMetricData` |
| Artefacts | S3 `ListObjectsV2` on the output prefix |
| Experiment grouping | Jobs grouped by a pipeline/algorithm tag |
| Cross-links | Job tags and `CustomerMetadataProperties` |

Read-only by construction — `List*`, `Describe*`, `Get*` only. No tracking
server, no database, no artifact store, nothing to back up. Return a distinct
error when an upstream is unavailable so the UI can tell "down" from "empty";
conflating those two is how a read layer starts looking like data loss.

The accepted tradeoff is named rather than hidden: CloudWatch metric retention
bounds how far back the per-step curves go. That is a real limitation and it was
worth it.

**The lesson:** the thing MLflow was providing was mostly a *view*. The data was
already in SageMaker and S3. Running a database to hold a second copy bought
nothing and cost an upgrade path.

### Grafana + Athena → charts drawn from the operational table

**Built:** Grafana with an Athena datasource, provisioned dashboards over a Glue
database of backtest results and drift reports.

**Removed.** The Glue database it queried did not exist in the account, so every
panel rendered empty — a dashboard that looks broken is worse than no dashboard,
because someone will eventually trust it. The charts moved into the application's
own API and frontend, reading the operational table directly. No extra container,
no load balancer, no Athena schema modelling, no second copy of the data.

**The lesson:** wiring a BI stack onto data that already has an API is two
integrations (a warehouse schema and a datasource) to display numbers you can
already fetch. Do it when several teams need self-service SQL — not to draw five
charts your own frontend can draw.

## Stage 15 — also available, also deliberately unused

SageMaker **Model Cards** and **Lineage** both work in the China Regions. Neither
is used in the reference platform. The substitute is one correlation id carried in
job tags and package metadata, plus explicit cross-links.

That is not laziness — it is the same rule applied. Lineage would have become a
second place where provenance lives, needing to agree with the first. One field
you control beats two stores you must reconcile.

The same reasoning retires Experiments as a *store*: its metric values **disagreed
with `DescribeTrainingJob` for the same metric on the same job** (1.62136 versus
1.20695). Two sources of one number is strictly worse than one source. Pick one,
declare it, and turn the other off (`pipeline_experiment_config=None`).

## Stage 15 — what *is* worth building

### Drift analysis — yes, and here is the part that is easy to get wrong

Model Monitor is closed to new customers everywhere, so drift analysis is
genuinely yours to build. Evidently OSS in a scheduled batch container is a
reasonable shape: a task that reads a reference frame and a current frame from S3,
computes per-feature drift over their intersecting columns, and writes an HTML
report, the raw metrics, and a small `latest.json` summary shaped exactly like the
API response — so the API never lists or parses S3 on a request.

Deployment shape: a Fargate task definition with no port mappings (it is a batch
job), launched by a scheduled EventBridge rule, task role scoped to read the data
bucket and write the report bucket. Keep the service desired count at zero; the
schedule is what runs it.

**The mistake to avoid, observed in the reference implementation:** it currently
compares the *training split* against the *test split*. That is a train-versus-test
distribution check, not production drift, and it will look healthy forever no
matter what production does.

Real drift monitoring needs the **inference inputs to be captured first**. Enable
data capture on the endpoint — that is part of endpoint configuration, not part of
the retired Model Monitor — then point the "current" side at the capture prefix and
the "reference" side at the training data. The analysis layer needs no change; only
the collection layer was missing. Build the collection before trusting the report.

**And close the loop.** A drift job that only writes a report is a monitor nobody
reads. If a threshold is configured, publish on breach. In the reference
implementation the drift alert topic exists and nothing ever publishes to it —
which is worse than having no topic, because the topic implies coverage.

### Your own AutoML — sometimes

Managed AutoML in the China Regions is previous-generation only (v2 is refused;
v1 works) and has no console. Building your own on Processing jobs with a
bring-your-own container is a real option, and the pattern that worked: an
LLM-driven AutoML agent whose model provider is any OpenAI-compatible endpoint
(because Bedrock is absent there), a contract requiring specific output artefacts,
success defined as exit-0 **and** those artefacts existing, per-iteration errors
collected into the run summary, and status written back by an EventBridge job-state
rule into a runs table.

Two disciplines it depends on: it executes generated code, so run it under a
least-privilege role in a restricted network; and take the job identity from
configuration rather than hardcoding a name prefix, or the second algorithm
silently breaks it.

A third, since the code is machine-written: its comments and printed output are
data too. A generated script asserting that it validated its own output does not
satisfy the exit-0-**and**-artefacts condition; check for the artefacts.

### Endpoint right-sizing — no, and say so

There is no Inference Recommender in the China partition and no substitute tool in
the reference platform either. Instance type and count are declared per
environment in the algorithm contract and revised by measurement.

Be honest about this rather than implying automation exists. Declaring the answer
in a reviewable contract is a legitimate engineering position; a home-grown
load-testing harness nobody maintains is not obviously better.

## Stage 16 — deciding to retrain, and where the new run re-enters

Monitoring exists to answer one question: **is this model still good enough to keep
using?** Retraining is what you do when the answer turns out to be no. Both live here
because the evidence that triggers a retrain is the evidence monitoring collects.

**Three triggers, declared in the contract, not judged ad hoc.**

| Trigger | Declared as | Why it needs declaring |
|---|---|---|
| Observed error breaches the gate | the same bound `evaluate-and-gate` used | otherwise "it got worse" is an opinion |
| Enough newly labelled data accumulated | a count or a span | otherwise retraining happens when someone remembers |
| The input distribution moved | a drift statistic and a bound | otherwise drift is noticed only after the error arrives |

The first is the strongest and the most often missing. A model whose live error can be
compared against its own pre-registered bound tells you it has degraded without anyone
inspecting a distribution — and the comparison is only possible if inference archived
the predictions and the later observed outcomes. That is why the archival requirement in
`release-and-serve` is not optional bookkeeping.

**Re-enter at stage 3 or stage 5, not at stage 1.** New data means the dataset must be
re-registered with a new immutable identity (stage 3); the same data reprocessed under a
changed policy means re-entering at processing (stage 5). Either way the contract, the
prediction time and the leakage policy carry forward unchanged unless a decision changed
them — and if one did, that is a new contract and says so.

**Keep the original test period as historical evidence and cut a new, later holdout.**
Re-using the old holdout across retrains turns it into a tuning set one run at a time,
and the decay is invisible: every individual run looks like it evaluated honestly.

**A retrain is a full run of this workflow, not a shortcut through it.** The
`retrain-existing` preset names which stages that means. A retrained model that skipped
the leakage screen or the baselines has skipped the two checks most likely to catch what
changed in the data.

## Stage 16 — the decision procedure

Before building any replacement, in order:

1. **Is the data already in an API you can call?** If yes, build a read layer, not
   a store. Most "we need a tracking server" cases end here.
2. **Would this become a second source of truth for something?** If yes, don't.
   Reconciling two stores costs more than the feature is worth.
3. **Is the missing piece the analysis, or the collection?** Build the collection
   first. A perfect analysis over the wrong inputs looks like it works.
4. **Who runs it at 3am?** A database, a load balancer and an upgrade path are the
   real price. Both removals above were removals of exactly that price.
5. **If you build it, close the loop.** A report nobody reads and an alert topic
   nobody publishes to are not monitoring.
