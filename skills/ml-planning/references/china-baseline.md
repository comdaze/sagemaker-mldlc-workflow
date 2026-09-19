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
| **A SageMaker or DLC image in the region's own registry** | resolve it with `image_uris.retrieve`; no public registry is touched at all, and it already carries the runtimes a slim base omits |
| **Your own ECR, pre-staged** | pull the base once by hand, push it to your account, then `FROM` that — see the measurement below |
| **AWS Public ECR** (`public.ecr.aws/docker/library/python:3.11-slim`) | in-partition, no credentials, no rate limit |
| **`nwcdlabs/container-mirror`** in `cn-northwest-1` ECR | covers `gcr.io`, `quay.io`, `k8s.gcr.io` — see the table below |
| DaoCloud (`docker.m.daocloud.io`) | a drop-in prefix for a Docker Hub reference, and **measured slow** — see below |
| Aliyun | **not a guessable address** — see below |

**Two corrections that came out of measuring rather than reasoning.**

**DaoCloud works and was the bottleneck.** A run instrumented its own slow build and found the
base-image layers, not pip and not the ECR push, were the cost:

```
DaoCloud image metadata        5.8 s
29.78 MB layer               ~25 s
13.88 MB layer                46 s and still not finished
```

At those rates the build had not reached `pip install` or `docker push` yet. **The fix is to stop
pulling it on every build**: pull the base image once, push it into your own ECR, and point
`FROM` at that. One slow pull instead of one per build, and the base stops being a network
dependency of every future build.

**Aliyun's Docker acceleration is not one address you can write down.** It is generally
per-account — an ACR instance's own endpoint, often with its own credentials — so
`registry.cn-hangzhou.aliyuncs.com` is **not** a working drop-in and inventing a
`registry.cn-…` host is the same mistake as hand-assembling an ECR URI. If a user has ACR, ask
them for their endpoint. Its PyPI mirror (`mirrors.aliyun.com`) is a different service and is
a public address; do not generalise from one to the other.

**A prerequisite that is easy to miss:** a CodeBuild project needs `privilegedMode` enabled to run
`docker build` at all. It is a project setting, not a permission, so the failure does not look like
one.

### The nwcdlabs mirror, and the rule for rewriting a path

`public.ecr.aws` carries the Docker Hub official library, but **not `gcr.io`, `quay.io` or
`k8s.gcr.io`** — and a base image or sidecar from one of those has nowhere else to come from
inside the partition. [nwcdlabs/container-mirror](https://github.com/nwcdlabs/container-mirror)
mirrors all four into one ECR registry in `cn-northwest-1`. The transformation is mechanical:

| Original | Mirrored |
|---|---|
| `[library/]repo:tag` (Docker Hub) | `<reg>/dockerhub/[library/]repo:tag` |
| `gcr.io/ns/repo:tag` | `<reg>/gcr/ns/repo:tag` |
| `k8s.gcr.io/repo:tag` | `<reg>/gcr/google_containers/repo:tag` |
| `quay.io/ns/repo:tag` | `<reg>/quay/ns/repo:tag` |
| `602401143452.dkr.ecr.us-west-2.amazonaws.com/repo:tag` (global ECR) | `<reg>/amazonecr/repo:tag` |

where `<reg>` is `048912060910.dkr.ecr.cn-northwest-1.amazonaws.com.cn`.

**Four things to know before depending on it**, none of which the path format tells you:

- **`docker login` is still required.** It is an ECR registry, so pull needs
  `ecr:GetAuthorizationToken` — the one exception is EKS or kops on EC2, where the node role
  covers it. In CodeBuild that means a *second* ECR login in `pre_build`: one to read the base
  image, one to push the result, and they are different registries.
- **The registry lives in `cn-northwest-1`.** Pulling from `cn-north-1` works but crosses regions,
  which costs transfer and adds latency to every build and every endpoint cold start.
- **Not every tag is there.** The repository publishes a `mirrored-images.txt` inventory and a
  request process for adding one. Check it before writing the path — a missing tag fails at pull
  time, inside a job, which is the most expensive place to find out.
- **It is a labs project, not an AWS service.** No SLA, and the wiki above was last indexed
  2025-05-20. **I read the documentation and did not pull an image**, so treat the registry id as
  documented rather than verified from here, and confirm it with one `docker pull` before a
  pipeline depends on it.

**`FROM python:3.9-slim` is the line to look for.** A real run's Dockerfile had exactly that plus
a bare `pip install -r requirements.txt`, submitted to CodeBuild in `cn-northwest-1`. Its `BUILD`
phase failed with `COMMAND_EXECUTION_ERROR` — and whether the mirrors were the cause is **not
established**, because the same run lacked `logs:CreateLogStream` and could not read the log that
would say. Both are worth fixing before the next attempt precisely because one hid the other.

**Rewriting a Dockerfile's sources is a change to what gets installed.** Say which mirror you
used and why, so the next person can tell a mirror problem from a code problem. A mirrored tag is
not a guarantee of the same digest as upstream — pin by digest for anything that must be
reproducible, exactly as `runtime-and-containers` says for the AWS registries.

### When no index works: vendor the wheels into the build context

The last resort, and a real run reached for it: download the wheels on a machine that *can* reach
an index, ship them inside the source archive, and install with `--no-index --find-links`.

```bash
pip download --no-deps --only-binary=:all: \
    --platform manylinux_2_17_x86_64 --python-version 39 \
    -d inference/wheels -r requirements.txt
# then, in the Dockerfile:
#   COPY wheels /tmp/wheels
#   RUN pip install --no-index --find-links=/tmp/wheels -r requirements.txt
```

**`--platform` and `--python-version` describe the image, not your laptop**, and getting them
wrong is the whole trap: the download succeeds, the archive looks right, and the install fails
inside the build with a message about no matching distribution. `--only-binary=:all:` is what makes
that mismatch surface at download time instead. Check the interpreter version in the base image
rather than assuming it matches the one you developed against.

This trades a network dependency for a maintenance one — the vendored set is now yours to keep
current, and it is invisible in `requirements.txt`. Prefer a mirror; use this when a package is not
on one.

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
