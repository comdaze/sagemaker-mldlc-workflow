# The AWS China partition, as a baseline

What SageMaker offers in `cn-north-1` and `cn-northwest-1`, and what it does not.
Read this in stage 1, before proposing a plan — not at execution time, when the
missing piece is a failed job.

## How to read the evidence column

Capability claims decay. Each row says how it was established, so a later reader
knows which ones to re-check:

- **measured** — an API call was made in the partition and its answer recorded
  (2026-09; the strongest form is `UnknownOperationException`, which is decisive)
- **run** — a real job or resource was created and reached a terminal state
- **documented** — AWS documentation says so; not independently verified

A verdict with no evidence marker is not a verdict. If you need one that is
missing, probe it read-only and record what you got.

## Absent

Do not put a stage in the plan that depends on any of these.

| Capability | Evidence |
|---|---|
| Amazon Bedrock | documented; no endpoint in either region |
| SageMaker HyperPod | measured |
| Managed MLflow | measured — `ListMlflowTrackingServers` → `UnknownOperationException` |
| Inference Recommender | measured |
| Serverless Inference | measured — refused |
| Shadow tests | measured |
| AutoML v2 (`CreateAutoMLJobV2`) | measured — v1 works, verified by a 25-minute run |
| Neuron images (Trainium / Inferentia) | documented; no such instances |
| `pytorch-smp` model-parallel images | documented |

Model Monitor is also unavailable for new use, but that is **not** a China
limitation: ten SageMaker features entered maintenance mode globally on
2026-07-30 and are closed to new customers, Model Monitor among them. Treat drift
monitoring as yours to build in every partition — see
`monitor-and-retrain`.

## Present and working

| Capability | Evidence |
|---|---|
| Training jobs, Processing jobs, Batch Transform | run |
| SageMaker Pipelines | run |
| Model Registry, including approval-status transitions | run |
| Built-in XGBoost, scikit-learn, Spark processing, AutoGluon images | run — present in the region's ECR and tracking global versions |
| Experiments, Model Cards, Lineage, Feature Store | measured — full CRUD |
| SageMaker Python SDK v3 against this control plane | run — a real `ml.m5.large` XGBoost job in `cn-north-1` via `ModelTrainer` reached `Completed`, produced `model.tar.gz`, reported `train:rmse` 0.30005, billed 134 s |

## Package and image sources: the default ones are not reachable enough

**Anything that downloads during a build needs a China source configured, and the failure mode is
a timeout rather than an error.** `pypi.org`, Docker Hub and the GitHub release assets many
installers fetch are all slow to unreachable from inside the partition — including from CodeBuild
and from a training job, which are in the partition too. A build that works on the author's
laptop and stalls in `cn-north-1` is the normal outcome, not bad luck.

This is not a convenience. A 20-minute timeout is charged as build minutes, and a partial layer
cache makes the retry behave differently from the first attempt, which is how an afternoon goes.

**pip** — set both, because a mirror that lacks a package must be allowed to fall through:

```dockerfile
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com
# or Tsinghua TUNA: https://pypi.tuna.tsinghua.edu.cn/simple
```

**uv** reads its own variable and ignores `PIP_INDEX_URL`:

```dockerfile
ENV UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/
```

Also pin `UV_PYTHON_INSTALL_MIRROR` if uv is to fetch an interpreter — that download comes from
GitHub, so it fails separately from the package index and the message points at Python, not at
the network.

**Base images** — in rough order of preference:

| Source | Notes |
|---|---|
| **AWS Public ECR** (`public.ecr.aws/docker/library/python:3.11-slim`) | in-partition, no credentials, no rate limit — the first thing to try |
| An AWS DLC or SageMaker image in the region's own registry | already the right shape for SageMaker; resolve it, never assemble the URI |
| Aliyun ACR mirror (`registry.cn-hangzhou.aliyuncs.com`) | reliable; namespace differs from Docker Hub's |
| DaoCloud (`docker.m.daocloud.io`) | a drop-in prefix for a Docker Hub reference |

**`FROM python:3.9-slim` is the line to look for.** A real run's Dockerfile had exactly that plus
a bare `pip install -r requirements.txt`, submitted to CodeBuild in `cn-northwest-1`. Its `BUILD`
phase failed with `COMMAND_EXECUTION_ERROR` — and whether the mirrors were the cause is **not
established**, because the same run lacked `logs:CreateLogStream` and could not read the log that
would say. Both are worth fixing before the next attempt precisely because one hid the other.

**Rewriting a Dockerfile's sources is a change to what gets installed.** Say which mirror you
used and why, so the next person can tell a mirror problem from a code problem.

## No console is a workflow constraint, not a missing feature

Studio has **no pages** for Experiments, Model Registry or AutoML in this
partition. Everything goes through control-plane APIs, which work fully.

This is worth stating positively rather than as a lament: an agent-driven workflow
calls APIs anyway. The constraint pushes toward exactly the shape this power
wants — but it does mean a human cannot fall back on clicking, so anything a
person must approve needs an approval surface you built. `release-and-serve`
assumes precisely that.

## Registry accounts are not one table

Never hand-assemble a registry URI; resolve it with
`sagemaker.core.image_uris.retrieve` (v3) and let it pair the account, the region
and the endpoint suffix together. That pairing is the whole problem.

| Region | Built-in algorithm account | Deep Learning Container account |
|---|---|---|
| `cn-north-1` | `450853457545` | `727897471807` |
| `cn-northwest-1` | `451049120500` | `727897471807` |
| global (e.g. `us-west-2`) | `246618743249` | `763104351884` |

Two traps in that table. The built-in-algorithm accounts **differ between the two
China regions**, so "the China account" is not a thing. And the DLC accounts
differ from the built-in ones entirely — code that learned one number and reused
it everywhere is wrong in a way that only shows up at image-pull time.

The endpoint suffix is `amazonaws.com.cn` here and `amazonaws.com` globally. Taking
the account from one partition and the suffix from the other produces a registry
host that does not exist:

```
763104351884.dkr.ecr.cn-northwest-1.amazonaws.com.cn/pytorch-training:2.0.0-cpu-py310
                                    ^^^^^^^^^^^^^^^^ China suffix
^^^^^^^^^^^^ global account
```

Where an SDK resolver is unavailable, keep the account and suffix in **one table
entry per region**, never two independent variables; resolve at runtime from the
concrete region; and **assert the pairing in a test** separately from asserting
each full URI — a test that only checks complete URIs also passes when someone
updates both strings wrongly at once.

## ARNs and identity

- ARNs are `arn:aws-cn:`. Write partition-agnostic patterns —
  `arn:(aws|aws-cn|aws-us-gov):` — rather than pinning one, or the platform cannot
  later move.
- **Never build a role ARN from a role name.** `aws iam list-roles` returns names
  without their IAM path, and a role at `role/service-role/<name>` does not exist
  at `role/<name>`. SageMaker's error for the wrong ARN reads like a trust-policy
  problem — "Could not assume role … ensure that the role exists and allows
  principal `sagemaker.amazonaws.com`" — which sends the investigation the wrong
  way. Read `Role.Arn` from `get-role`.
- Pass an explicit `Session(boto_session=...)` to SDK v3 constructors. With
  `sagemaker_session=None` the SDK builds its own and logs `No region provided.
  Using default region.` — in a multi-partition setup that is a real hazard.

## JumpStart

The model catalogue is smaller and differs between the two regions: roughly 427
models in `cn-northwest-1` and 423 in `cn-north-1`, against about 743 globally
(measured 2026-09). Nova and Gemma are absent; the Qwen family is comparatively
well represented. A global model id is not guaranteed to resolve here — check
rather than assume.

## What this means for the plan

Three habits, in order of how often they matter:

1. **Read the caller ARN, never infer the partition from a region name.**
2. **State the applicable limits in `PLAN.md` itself**, so a resumed session and a
   reviewer both see them without re-deriving.
3. **When you need a verdict this file does not carry, probe read-only and record
   the evidence marker.** Parameter validation happens *before* the region
   availability check, so a deliberately malformed call returning
   `ValidationException` proves nothing about availability — that is how
   `CreateAutoMLJobV2` was once misread as available. Only
   `UnknownOperationException` is decisive; a business-logic rejection is close;
   throttling proves nothing. And asynchronous validation can really create
   resources, so probes are not always free.
