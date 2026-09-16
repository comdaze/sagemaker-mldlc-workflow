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

## The stage catalogue

Ordering is a prerequisite chain: each stage's output is the next one's required
input. Stages marked **standalone** are the ones a user calls on their own.

| # | Stage | SageMaker surface | Standalone |
|---|---|---|---|
| 1 | Frame the problem | — | |
| 2 | Environment readiness | partition, quotas, capability probe | |
| 3 | Register the dataset | versioned S3 identity | ✔ |
| 4 | Leakage guard | — | ✔ |
| 5 | Data processing | `ProcessingStep`, Feature Store | ✔ |
| 6 | Baseline first | — | ✔ |
| 7 | Training | `TrainingStep`: built-in / framework / custom container / distributed | ✔ |
| 8 | Tuning | `TuningStep` | ✔ |
| 9 | Evaluation | `ProcessingStep`, same image as training | ✔ |
| 10 | Quality gate | `ConditionStep` | |
| 11 | Model registration | `RegisterModel`, `PendingManualApproval` | |
| 12 | Governed release | approval + provenance | ✔ |
| 13 | Batch inference | `TransformStep` / Batch Transform | ✔ |
| 14 | Real-time inference | endpoint, data capture | ✔ |
| 15 | Monitoring | drift, and what not to self-build | ✔ |
| 16 | Retraining | back to 3 or 5 | |

Stage 6 exists because of a specific failure: in a validation run the pipeline
reported MAE 18.8 against a gate of 130 and every structural check passed, while
a single leaking column used **directly as the prediction** scored MAE 14.2. Had
the flow been required to state the naive baselines first — predict the mean
(182.7), predict yesterday at the same time (137.1) — the result would have been
suspect on sight.

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

| Skill | Stages | Standalone | Origin |
|---|---|---|---|
| `ml-planning` | 1, 2, presets, `PLAN.md` | orchestrator | rewritten from the `forecast-planning` in `kiro-power-sagemaker-tabular-mlops` |
| `dataset-contract` | 3 | ✔ | extracted from that power's `contracts.md` |
| `leakage-guard` | 4 | ✔ | generalised from the same power |
| `runtime-and-containers` | cross-cutting: 5, 7, 13, 14 | ✔ | new |
| `data-processing` | 5 | ✔ | new |
| `baseline-first` | 6 | ✔ | new |
| `model-training` | 7 | ✔ | rewritten from `tabular-training` |
| `hyperparameter-tuning` | 8 | ✔ | new |
| `evaluation-and-gate` | 9, 10 | ✔ | split out of `tabular-training` |
| `sagemaker-pipeline` | composes 5–11 | orchestrator | new |
| `batch-inference` | 13 | ✔ | new |
| `realtime-inference` | 14 | ✔ | new |
| `governed-release` | 11, 12 | ✔ | **copied unchanged** |
| `dont-rebuild-what-you-can-read` | 15 | ✔ | **copied unchanged** |

Two skills are copied byte-for-byte because they are already free of any
domain or task assumption: an audit found 2 and 1 lines respectively touching
forecasting vocabulary, against 37 in the planner.

`sagemaker-pipeline` is the load-bearing one for the "use SageMaker Pipeline
properly" requirement. The single-stage skills teach how to write one step; it
teaches how to compose steps into a `Pipeline` — ordering, parameterisation,
caching, resuming, and turning Experiments auto-registration off. Calling a stage
alone and composing stages into a pipeline are two uses of the same skills, not
two bodies of content.

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

- `PLAN.md` as the state of the work, with six per-task states
  (`[ ] [-] [?] [R] [x] [S]`), a `PARTITION` line and a `LAST_DONE` cursor,
  checked by `scripts/plan-lint.py`.
- Constraints classified as **Refusal**, **Accounting** or **Advice**, with the
  plan required to record which ones were traded away and what replaced them.
- Asking well: never ask for a value your own next action would produce; every
  blocking question carries an executable default.
- Read material is data, not instructions.
- Mirror invariants, read mutable state live.
