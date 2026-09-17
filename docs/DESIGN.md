# Design record

Why this power is shaped the way it is, and what was measured rather than assumed.

Everything under "Measured" was verified by running it on 2026-09-16 against
`cn-north-1` and PyPI. Everything under "Assumed" is explicitly not verified.
That split is the point of this file: a later reader must be able to tell which
claims to re-check.

## What this is

An AI-coding-driven ML development workflow for Amazon SageMaker, in the sense
that `aidlc-workflows` is one for software development: a planner that composes a
workflow from named stages, plus one skill per stage that can also be invoked on
its own.

**In scope:** traditional machine learning and deep learning — tabular
regression and classification, time-series forecasting, and DL trained in a
framework or custom container.

**Out of scope:** large-model fine-tuning. That is a different track with its own
contracts, and AWS already ships an official skill for it (see "Neighbours").

**Verified baseline:** the AWS China partition. The content is general — the
skills teach SageMaker, not China — but every capability claim carries a China
verdict, and the capability matrix lives in
`skills/ml-planning/references/china-baseline.md`. The reason for that emphasis
is not patriotism about a region; it is that a workflow which quietly does not
run where its user is, is worse than one that says so.

## What may travel into a skill, and what may not

This power is validated against real projects, and that creates a specific way for
it to fail: a skill written from one project's code teaches the shape of that
project while looking like general guidance. The user of a churn model or an image
classifier then receives rules fitted to a market-price dataset, with nothing in
the text to warn them.

So there is one line, and it is narrow enough to apply without judgement:

> **A measurement may be the ARGUMENT for a constraint. It may never be the
> CONTENT of one.**

| Admissible | Not admissible |
|---|---|
| "One column used directly as the prediction scored MAE 14.18 against a target whose standard deviation was 232" — why the leakage screen is a refusal | A column name, a schema, a table of allowed and forbidden fields from some dataset |
| "A gate of 130 passed a model at 18.8 while a single column scored 14.2" — why stage 6 exists | "Split by trading day, 96 rows per day" — one dataset's grain presented as a rule |
| "A run attributed ten stages to skills that do not own them" — a workflow defect any dataset would produce | A domain unit, a market, a sensor layout, a business threshold |
| A measured platform fact: registry accounts, an SDK import that fails, a container port | An instance type or hyperparameter that suited one model |

The test for the first column is whether a reader could reconstruct the project
from it. The test for the second is whether the rule survives changing the
problem.

**A validation run's role is to be a test case, not a source.** It shows that a
rule is needed, and it shows whether a rule that exists actually fires. It does not
supply the rule's text. The nine stages a real 17-task run implemented from first
principles produced 2,683 lines of working code, in the China partition, against a
live control plane — and none of it is skill material, because all of it answers
"how do I process this dataset" rather than "how does a SageMaker processing job
work".

### The three-domain check

Every skill is checked against three problems that share nothing but SageMaker:

1. **Tabular classification** — churn or fraud, no time axis, class imbalance.
2. **Time-series forecasting** — a horizon, a prediction time, chronological splits.
3. **Unstructured deep learning** — images or text, a framework container, GPU
   instances, no feature columns at all.

A rule that cannot be stated for all three is a rule borrowed from one project. What
happens to it next is the subject of the next section, and it is not deletion.

This is how the audit that let two skills be copied byte-for-byte was framed, and
it caught a real leak: 37 lines of forecasting vocabulary in the planner, against 2
and 1 in the two that travelled unchanged.

### Where domain material goes: `SKILL.md` decides, `references/` explains

Failing the three-domain check is not grounds for deleting content. Correlation
thresholds are the right leakage test for a scalar input and meaningless for an
image; publication-time-versus-observation-time is essential for a temporal source
and absent from a churn table. All of it is true and useful. It just is not
universal, and a skill body that presents it as universal misleads two readers out
of three.

The skill format already answers this. Quoting the authoring guidance this power
follows:

> **Avoid duplication**: Information should live in either SKILL.md or references
> files, not both. **Prefer references files** for detailed information unless it's
> truly core to the skill […] Keep only essential procedural instructions and
> workflow guidance in SKILL.md; **move detailed reference material, schemas, and
> examples to references files.**

So each skill is two layers, and the split is a specific one:

| Layer | Holds | Loaded |
|---|---|---|
| `SKILL.md` | What the rule is. Whether it is a refusal. What happens when it fires. The decision procedure. | Every time the skill triggers |
| `references/<domain>.md` | How to measure it in one modality. Statistics, thresholds, probe constructions, per-domain examples. | Only when the agent judges it needed |

The dividing question is **"what do I do" versus "how do I measure it"**. The first
is the same in all three domains and belongs in the body. The second differs per
modality and belongs in a reference.

**A refusal never moves to a reference.** This is the failure mode the split is
designed to prevent, and it was proposed in a real audit: because correlation does
not apply to images, generalise the rule to "unusually predictive inputs require
provenance review". That trades this power's core mechanism for tidiness. A
constraint written as a refusal survives into the artefacts; the same constraint
written as a review suggestion holds by luck. So the body keeps *this is a refusal
and it does not proceed*, and the reference supplies *and here is the statistic for
your modality*.

Two consequences worth stating, because both are improvements rather than costs.
Domain material can be **more** detailed once it is out of the body, since it no
longer competes for the context every invocation pays for. And a reader working on
one of the other two domains never sees it — which is the actual goal, since the
harm was never that the content existed.

`ml-planning/references/china-baseline.md` is the pattern already in use: the
capability matrix lives there, the body carries one instruction to read it before
proposing anything. Where a reference grows past roughly ten thousand words, put
grep patterns in the body rather than expecting it to be read whole.

## The stage catalogue

Ordering is a prerequisite chain: each stage's output is the next one's required
input. Stages marked **standalone** are the ones a user calls on their own.

**Sixteen stages, eight skills.** The two are deliberately not one-to-one — see
"Why the stages and the skills are not one-to-one" below.

| # | Stage | Owning skill | SageMaker surface | Standalone |
|---|---|---|---|---|
| 1 | Frame the problem | `ml-planning` | — | |
| 2 | Environment readiness | `ml-planning` | partition, quotas, capability probe | |
| 3 | Register the dataset | `data-pipeline` | versioned S3 identity | ✔ |
| 4 | Leakage guard | `leakage-guard` | — | ✔ |
| 5 | Data processing | `data-pipeline` | `ProcessingStep`, Feature Store | ✔ |
| 6 | Baseline first | `train-and-tune` | — | ✔ |
| 7 | Training | `train-and-tune` | `TrainingStep`: built-in / framework / custom container / distributed | ✔ |
| 8 | Tuning | `train-and-tune` | `TuningStep` | ✔ |
| 9 | Evaluation | `evaluate-and-gate` | `ProcessingStep`, same image as training | ✔ |
| 10 | Quality gate | `evaluate-and-gate` | `ConditionStep` | |
| 11 | Model registration | `release-and-serve` | `RegisterModel`, `PendingManualApproval` | |
| 12 | Governed release | `release-and-serve` | approval + provenance | ✔ |
| 13 | Batch inference | `release-and-serve` | `TransformStep` / Batch Transform | ✔ |
| 14 | Real-time inference | `release-and-serve` | endpoint, data capture | ✔ |
| 15 | Monitoring | `monitor-and-retrain` | drift, and what not to self-build | ✔ |
| 16 | Retraining | `monitor-and-retrain` | back to 3 or 5 | |

Two cross-cutting skills own no numbered stage because they apply at several:
`runtime-and-containers` (what runs your code, at stages 5, 7, 9, 13, 14) and
pipeline composition, which belongs to `ml-planning` — the planner decides the stage
sequence, and composing that sequence into a `Pipeline` object is the same decision
expressed as code rather than as `PLAN.md`.

The machine-readable form is `skills/ml-planning/references/stage-catalogue.txt`,
which `plan-lint.py` reads to derive each task's expected owner and `validate.py`
cross-checks against both this table and `skills/`. Three copies of one list drift;
two of the three are checked.

Stage 6 exists because of a specific failure: in a validation run the pipeline
reported MAE 18.8 against a gate of 130 and every structural check passed, while
a single leaking column used **directly as the prediction** scored MAE 14.2. Had
the flow been required to state the naive baselines first — 182.7 for the mean, 137.1
for the simplest domain-valid reference — the result would have been suspect on sight.

## Why the stages and the skills are not one-to-one

The first version of this design made them one-to-one: sixteen stages, fourteen
skills. That was wrong, and the measurements say so from three directions.

**The format has a cap.** The authoring guidance keeps a `SKILL.md` body under 500
lines because it loads on every trigger. The five phase-one skills average 328 lines,
`ml-planning` is 591 and already over, and fourteen skills at that average projects to
roughly 4,600 lines of body. Eight skills sized to fit projects to about 2,160.

**The established frameworks group.** CRISP-DM has organised roughly two dozen tasks
under six phases since 1996, and CRISP-ML(Q) and Oracle's ML process both keep six.
The stage list is a checklist and a checklist is cheap; a file per checklist item is
not. Sixteen stages map onto the six standard phases without remainder, which is why
the stage numbers survived the consolidation unchanged.

**AI-DLC does more with less text.** The power this one is modelled on ships 39 skills
whose median body is 43 lines, 2,058 in total, covering a larger lifecycle. It manages
that because each stage skill is a thin wrapper that delegates to an engine — the rules
live in code and data, not in prose. That is the architectural lesson, and it is why
each new skill here ships with a script rather than a longer description of one.

## Scope presets

Four named subsets, chosen statically rather than compiled. `aidlc-workflows`
transposes a stage grid from each stage's own `scopes:` tag and validates it with
a drift guard; at four presets and sixteen stages that machinery costs more than
it returns.

| Preset | Stages |
|---|---|
| `full-lifecycle` | 1–16 |
| `retrain-existing` | 3, 5, 7, 9, 10, 11, 12 |
| `inference-only` | 13 or 14 |
| `data-prep-only` | 3, 4, 5 |

## Skills

| Skill | Stages | Body | Standalone | Status |
|---|---|---:|---|---|
| `ml-planning` | 1, 2, presets, `PLAN.md`, pipeline composition | 591 → ~320 | orchestrator | exists, over the 500-line cap, absorbs pipeline composition |
| `leakage-guard` | 4 | 260 | ✔ | exists, two-layer, unchanged by this consolidation |
| `runtime-and-containers` | cross-cutting: 5, 7, 9, 13, 14 | 309 | ✔ | exists, unchanged |
| `data-pipeline` | 3, 5 | ~200 | ✔ | to build |
| `train-and-tune` | 6, 7, 8 | ~250 | ✔ | to build |
| `evaluate-and-gate` | 9, 10 | ~200 | ✔ | to build |
| `release-and-serve` | 11, 12, 13, 14 | 306 → ~400 | ✔ | rename of `governed-release`, absorbing 13 and 14 |
| `monitor-and-retrain` | 15, 16 | 178 → ~220 | ✔ | rename of `dont-rebuild-what-you-can-read`, absorbing 16 |

Eight skills, roughly 2,160 lines of body when complete, every file inside the cap.
The fourteen-skill version projected 4,600.

Two properties of the earlier design are preserved and worth stating, because a
consolidation is where they get lost. Every stage marked standalone is still callable
alone: a skill covering four stages is still invoked for just batch inference, and the
scope presets still name stages rather than skills. And composing stages into a
`Pipeline` is still two uses of the same content rather than two bodies of it — that
was `sagemaker-pipeline`'s reason for existing, and folding it into `ml-planning`
keeps the property while removing the file.

Two costs are real. `dont-rebuild-what-you-can-read` is at this writing still
**byte-identical** to its source in `kiro-power-sagemaker-tabular-mlops`, which was
deliberate: it made the predecessor a regression comparison. Renaming and extending it
ends that. (`governed-release` already diverged by 48 lines when it gained a
`gitCommit` fallback, so that property was gone there already.) And the skills carry 49
cross-references to each other by name; every one pointing at a renamed skill has to
move with it, which `validate.py` does not check — only the catalogue cross-check is
mechanical.

## Roadmap

Three of eight skills exist and one of the three is over the line cap. The table above
is the design; this is the order it gets built in and the reason for that order. It
lives here rather than in a conversation because a workflow that insists decisions be
written where a later reader can find them should not except its own.

Two mechanical progress indicators, so the backlog is not a prose list anyone has to
trust. `validate.py` fails while `stage-catalogue.txt`, `skills/` and the stage table
disagree — it is red right now, and the red names exactly what is missing. And
`plan-lint.py` prints `N stage(s) owned by no skill yet` on every run, so the gap is
visible from inside a plan.

### Step 1 — record the design, and let the checks go red

Rewrite `stage-catalogue.txt` and this document first. `validate.py` then fails with
one line per skill that exists but owns no stage, and one warning per catalogue name
the stage table does not mention. That output is the todo list, generated rather than
maintained.

### Step 2 — bring `ml-planning` inside the cap

591 lines against a 500-line limit, and it has to absorb pipeline composition on top.
This is first because it is the only current violation, and because every other skill
cross-references it.

### Step 3 — the two renames

`governed-release` → `release-and-serve`, absorbing batch and real-time inference.
`dont-rebuild-what-you-can-read` → `monitor-and-retrain`, absorbing the retraining
decision. Both carry their existing content forward; the work is the new stages and
the 49 cross-references.

### Step 4 — the three new skills, in dependency order

```
data-pipeline → train-and-tune → evaluate-and-gate
```

Baselines sit inside `train-and-tune` and come before training within it, because a
model cannot be judged before something exists to judge it against and a run that
trains first treats whatever number it gets as the result.

Each ships with a script from the start, not a description of one:
`leakage-screen.py`, `contract-check.py`, `baselines.py`, `quality-gate.py`. A script
is deterministic, it is executable without loading into context, and it is the only
form in which a rule becomes a refusal rather than a paragraph. `plan-lint.py` is the
existing proof: 541 lines of code that replaced prose nobody would have checked.

### What gates each skill

A skill ships when all five hold:

1. **It passes the three-domain check**, and everything that failed the check sits in
   `references/` rather than in the body or the bin.
2. **Its refusals are in `SKILL.md`, not in a reference or a comment**, and each has a
   test that proves it fires. A screen that has never refused anything is
   indistinguishable from a screen with a sign error, and both report PASS.
3. **The body is under 500 lines** and carries no statistic, threshold or probe
   construction specific to one modality. Those are the reference layer's job.
4. **`validate.py` and `plan-lint.py` pass**, including the catalogue cross-check.
5. **`ml-planning`'s "not yet implemented" list no longer names it.**

Four of the five are checkable by someone other than the author. The third needs
judgement, and this is the cheap version of it:

```bash
grep -niE 'correlation|AUC|mutual information|ml\.[a-z0-9]+\.[a-z]|rng\.normal|0\.9[0-9]|chronological|timestamp' \
     skills/*/SKILL.md
```

A hit is not automatically a violation — a body may say *a bound exists and crossing it
refuses* without saying what the bound is. But every hit has to be argued for, and most
cannot be. On the three existing skills it returns 4 in `leakage-guard` (the measured
argument, a failure-mode row, a strategy enum), 6 in the skill becoming
`release-and-serve`, 3 in `ml-planning`, 1 in the skill becoming `monitor-and-retrain`,
and 0 in `runtime-and-containers` — which is the only one whose measurement layer
already lives elsewhere. That 0 is the target shape.

**Every new skill is written in two layers from the start.** Writing one flat and
splitting it later is how phase one came to need an audit: the body is the path of
least resistance, so material lands there and stays. The audit found 30 items in one
242-line skill, about ten of them the same defect repeated.

## Runtime modes: BYOS, BYOC, BYOM

`runtime-and-containers` owns the choice, because it is the same decision on
three surfaces (processing, training, inference) and getting it wrong is
expensive on all three.

| Mode | You supply | Cost |
|---|---|---|
| Built-in algorithm | data and hyperparameters | the algorithm is fixed |
| Script mode (BYOS) | `entry_point`, `source_dir`, `dependencies`, on an AWS framework image | only PyPI packages, or what the image already has |
| Extend a prebuilt image | a Dockerfile `FROM` an AWS image | an image to maintain |
| Custom container (BYOC) | a full image in ECR | the SageMaker contract, below |
| BYOM | trained artefacts only | no training |

The BYOC contract, from the SageMaker developer guide: the container directory
layout is `/opt/ml/{input,model,code,output,failure}`; training writes the final
model to `/opt/ml/model`; inference reads the model from `/opt/ml/model` and its
code from `/opt/ml/code`; training runs a script named `train` by default, or the
entry point named in `AlgorithmSpecification`'s `ContainerEntrypoint` /
`ContainerArguments`; an inference container serves on **port 8080** and must
answer `POST /invocations` and `/ping`, within 60 s for a regular response and
8 minutes for a streaming one, with a 25 MB payload ceiling. The SageMaker
Training Toolkit and Inference Toolkit satisfy these conventions for you.

## SDK version: v3 is the line, and it is still moving

### Measured

| Fact | Evidence |
|---|---|
| v3 is the active line | 3.22.0 released 2026-09-15; 3.0 released 2025-11-20; 22 minor releases in ten months |
| v2 is frozen, not retired | last new minor 2.257.0 on 2026-02-03; patches only since, last 2.257.6 on 2026-08-10. No official end-of-support date was found |
| `pip install sagemaker` gives v3 | resolves to 3.22.0 today, so using v2 means pinning `<3` deliberately |
| v3 requires Python | `>=3.10` |
| **v3 is a package rewrite, not an upgrade** | the top level exports nothing; the packages are `ai_registry`, `core`, `lineage`, `mlops`, `serve`, `train` |
| v3 works against the China control plane | a real `ml.m5.large` XGBoost job in `cn-north-1` via `ModelTrainer` reached `Completed`, produced `model.tar.gz`, reported `train:rmse` 0.30005, billed 134 s |

Seven v2 idioms, each tested against 3.22.0, all of which fail:

```
from sagemaker import image_uris                      ImportError
from sagemaker.estimator import Estimator             ModuleNotFoundError
from sagemaker.processing import ScriptProcessor      ModuleNotFoundError
from sagemaker.workflow.pipeline import Pipeline      ModuleNotFoundError
from sagemaker.workflow.steps import ProcessingStep   ModuleNotFoundError
from sagemaker.workflow.condition_step import ...     ModuleNotFoundError
from sagemaker.sklearn.estimator import SKLearn       ModuleNotFoundError
```

`sagemaker.__version__` is also gone — use `importlib.metadata.version`.

New locations: `sagemaker.core.image_uris`; `sagemaker.core.{Processor,
ScriptProcessor, FrameworkProcessor, Transformer}`; `sagemaker.train.ModelTrainer`;
`sagemaker.serve.ModelBuilder`. Pipeline machinery is under
`sagemaker.core.workflow/` and `sagemaker.mlops/workflow/` — `sagemaker.mlops`
does **not** export `Pipeline` at its top level.

**v3 is not a stable target either.** In 3.22.0, `sagemaker.train.configs` is a
deprecation shim that warns the canonical path is
`sagemaker.core.training.configs` and that the shim will be removed. Skills must
use canonical paths.

### Decision

v3 is the main line. The consequence is a **version gate**, not a pair of
templates: `spec.runtime.sdk` in the contract, checked at stage 2 against
`importlib.metadata.version("sagemaker")`, failing hard on a mismatch. Two full
sets of idioms would double the content and drift; one gate is cheap and its
refusal is informative.

### Three traps this cost us to find

- **Never hand-assemble an ARN.** `aws iam list-roles` returns a name without its
  IAM path. A role at `role/service-role/<name>` does not exist at
  `role/<name>`, and SageMaker's error — "Could not assume role … ensure that
  the role exists and allows principal `sagemaker.amazonaws.com`" — reads like a
  trust-policy problem. All four candidate roles had clean trust policies, which
  sent the investigation the wrong way twice. Read `Role.Arn` from `get-role`.
  This is the general form of the rule about never assembling an image URI.
- **Pass an explicit session.** With `sagemaker_session=None`, `ModelTrainer`
  builds its own and logs `No region provided. Using default region.` throughout.
  In a multi-partition setup that is a real hazard: construct
  `Session(boto_session=...)` and pass it.
- **`py_version` changed.** For xgboost and sklearn, `py_version="py3"` is now
  ignored with an informational log; for pytorch it raises
  `ValueError: Unsupported Python version: py3`.

### Registry accounts are not one table

Resolved by `sagemaker.core.image_uris.retrieve`, which is why it must never be
hand-assembled:

| Region | Built-in algorithm account |
|---|---|
| `cn-north-1` | `450853457545` |
| `cn-northwest-1` | `451049120500` |
| `us-west-2` | `246618743249` |

These are distinct from the Deep Learning Container accounts (`763104351884`
globally, `727897471807` in China), and the built-in accounts **differ between
the two China regions** — so "the China account" is not a thing.

## No MCP server, on purpose

Three reasons, in increasing order of how much they matter.

1. **The dedicated server does not help here.**
   `awslabs.sagemaker-ai-mcp-server` is at 1.1.0 and covers SageMaker HyperPod
   only, which is the one capability the China partition lacks entirely. Zero
   usable tools for this power's verified baseline.
2. **That whole line has an official successor.** `awslabs/mcp` now states that
   the Agent Toolkit for AWS "is the successor to the MCP servers, plugins, and
   skills available on AWS Labs" and that "over time, the most useful projects
   here will move into Agent Toolkit for AWS". Welding an awslabs MCP into
   `mcp.json` is betting on a line its own maintainers call superseded.
3. **An MCP does not solve this power's problem.** MCP servers wrap AWS *APIs*;
   the hard part here is generating *correct code*. Seven dead imports, an ARN
   path, a session that does not inherit its profile, and a config shim mid-move
   are all code-generation failures. No MCP prevents any of them. A skill that
   names the canonical paths and a version gate do.

### On the Agent Toolkit for AWS

Went GA on 2026-05-06 as the AWS-supported path forward, bundling the AWS MCP
Server (also GA) with curated skills across three plugins — `aws-core`,
`aws-agents`, `aws-data-analytics` — and working with Kiro, Claude Code, Cursor
and Codex. Its MCP server exposes `call_aws` over 15,000+ AWS API operations
using the caller's own IAM credentials, plus `search_documentation` and
`read_documentation`, and supports IAM condition keys so agent actions can be
scoped by a standard IAM policy.

Two things follow. It may become the distribution channel that matters more than
a power registry, which is worth watching. And its managed endpoint is
`https://aws-mcp.us-east-1.api.aws/mcp` — reachable (an unauthenticated `GET`
returns 405, so the host is up and wants `POST`) but requiring credentials that a
China-partition principal cannot sign for that endpoint. So it does not replace
this power for the verified baseline.

### AWS Knowledge MCP: reachable, and worth recommending as optional

`https://knowledge-mcp.global.api.aws` needs no AWS credentials and answers a
real MCP handshake:

```
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-03-26",
 "capabilities":{"tools":{"listChanged":false}},
 "serverInfo":{"name":"AWSKnowledgeMCP","version":"1.0.0"}}}
```

DNS 31 ms, connect 197 ms. It addresses precisely the failure this document is
full of — the SDK moved and a model's training data did not.

**Assumed, not measured:** that this holds from a customer network inside China.
The probe ran from one machine whose egress path is not characterised here, and
the endpoint is CloudFront-fronted. Anyone depending on it should re-run the
handshake from their own network. It is therefore documented as an optional
companion, not bundled in `mcp.json` — which also cannot load conditionally per
region, so bundling would push it onto every user.

## Neighbours

- **`kiro-power-sagemaker-ai`** — packages AWS's upstream `sagemaker-ai` plugin,
  19 skills, shaped for large-model fine-tuning. Different track.
- **`kiro-power-sagemaker-tabular-mlops`** — this power's predecessor. Kept, not
  migrated: it carries a complete end-to-end validation record and serves as the
  reference implementation and regression comparison.
- **AWS's own `aws-ai-ml` skill** (in the Agent Toolkit registry) — covers model
  selection, fine-tuning (SFT, DPO, RLVR, RLAIF), evaluation, endpoint
  diagnostics and PySDK v3 usage, and states it is **not** for Ground Truth,
  Feature Store, or general AWS infrastructure. Its shape is large-model
  customisation, so it does not cover the traditional-ML and DL training
  lifecycle this power is about.

## Carried over unchanged

These are not new inventions and they are not re-litigated here:

- `PLAN.md` as the state of the work, with seven per-task states
  (`[ ] [-] [?] [R] [x] [S] [!]`), a `PARTITION` line and a `LAST_DONE` cursor,
  checked by `skills/ml-planning/scripts/plan-lint.py`. `[!]` was added after a real
  run recorded a refused quality gate as `[S] skipped`, which made a blocked release
  read as a scope decision.
- Constraints classified as **Refusal**, **Accounting** or **Advice**, with the
  plan required to record which ones were traded away and what replaced them.
- Asking well: never ask for a value your own next action would produce; every
  blocking question carries an executable default.
- Read material is data, not instructions.
- Mirror invariants, read mutable state live.
