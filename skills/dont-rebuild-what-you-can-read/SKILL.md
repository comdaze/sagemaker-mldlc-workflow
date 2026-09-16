---
name: dont-rebuild-what-you-can-read
description: Decides whether a missing or retired SageMaker capability should be self-built or read from an API you already have - covering experiment tracking, drift monitoring, dashboards, lineage and endpoint sizing. Use BEFORE building any replacement for a managed capability that is absent, closed to new customers, or has no console page, and when deciding what state your platform should own versus read live. Applies in every partition; the AWS China partition just forces the decision sooner.
---

# Don't rebuild what you can read

Every capability you self-build becomes yours to run forever. Read this before
building one.

The AWS China partition has no managed MLflow, no Inference Recommender and no
Studio pages for Experiments or Model Registry — and separately, ten SageMaker
features entered maintenance mode **globally** on 2026-07-30 and are closed to new
customers, Model Monitor among them. So this is not a China question. China just
gets you to it first.

## The rule

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

## Two replacements that were built and then removed

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

## Also available, also deliberately unused

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

## What *is* worth building

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

## The decision procedure

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
