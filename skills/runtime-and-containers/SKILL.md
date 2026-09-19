---
name: runtime-and-containers
description: Chooses and configures what actually runs your code on SageMaker - a built-in algorithm, script mode on an AWS framework image, an extended image, a fully custom container (BYOC), or a model brought as artefacts only (BYOM) - and resolves the container image correctly per partition. Covers the SageMaker container contract, the SDK v2/v3 split and the version gate, and distributed training images. Use when setting up processing, training or inference runtime, writing a Dockerfile for SageMaker, resolving an image URI, or diagnosing a container that will not start.
---

# Runtime and containers

The same decision appears on three surfaces — processing, training, inference — and
getting it wrong is expensive on all three. So it lives here once, and
`data-processing`, `model-training`, `batch-inference` and `realtime-inference` all
defer to it.

Two things must be settled before any code is generated: **which SDK major version
you are writing for**, and **which of the five runtime modes you are in**. The first
is a gate because getting it wrong is an `ImportError` on line one. The second is a
decision because all five are legitimate.

## The SDK version gate

Declare it in the contract and check it. This is a refusal, not a note.

```yaml
spec:
  runtime:
    sdk: ">=3.22,<4"
```

```python
import importlib.metadata as md
installed = md.version("sagemaker")   # note: sagemaker.__version__ was REMOVED in v3
```

Fail hard when the installed version does not satisfy the declared range. The reason
is not caution — it is that **v3 is a package rewrite, not an upgrade**. The v3 top
level exports nothing; its packages are `ai_registry`, `core`, `lineage`, `mlops`,
`serve`, `train`. Measured against 3.22.0, every one of these v2 idioms fails:

```
from sagemaker import image_uris                      ImportError
from sagemaker.estimator import Estimator             ModuleNotFoundError
from sagemaker.processing import ScriptProcessor      ModuleNotFoundError
from sagemaker.workflow.pipeline import Pipeline      ModuleNotFoundError
from sagemaker.workflow.steps import ProcessingStep   ModuleNotFoundError
from sagemaker.workflow.condition_step import ...     ModuleNotFoundError
from sagemaker.sklearn.estimator import SKLearn       ModuleNotFoundError
```

`pip install sagemaker` resolves to v3 today, so a project that wants v2 must pin
`<3` deliberately. v2 is frozen rather than retired — its last new minor was
2.257.0 in February 2026, patches only since — so nothing new lands there.

### The refusal must carry its own remedy

A gate that fires and then stops at "the version does not match" is technically
working and practically useless. The message must contain **the command that fixes
it**, resolved against what you actually found, not a general instruction to
upgrade:

```
Environment gate failed.
  declared   spec.runtime.sdk = ">=3.22,<4"
  installed  2.256.1

  Fix:  pip install -U 'sagemaker>=3.22,<4'

  Or, to keep v2 deliberately, change the contract to "<3" and accept that this
  power's code templates are written for v3 — the two are not interchangeable, and
  the v2 import paths above are the reason.
```

Three properties make that worth the extra lines. It names **both** legitimate
resolutions, because pinning v2 on purpose is a real choice and not an error. The
command is complete and copy-pasteable, with the range taken from the contract
rather than invented. And it says what changing the contract costs, so the cheaper
option is not silently the more attractive one.

**Prefer a project environment over mutating the global interpreter.** A trial run
proposed this unprompted and it is the better default: an upgrade that only affects
this project is a smaller thing to consent to, and it cannot break another project
that needs v2.

```
  Fix (recommended — scoped to this project):
      python3 -m venv .venv && ./.venv/bin/pip install 'sagemaker>=3.22,<4'

  Fix (global, affects every project using this interpreter):
      python3 -m pip install -U 'sagemaker>=3.22,<4'
```

State the side effects either way: this changes local Python dependencies and
**creates, modifies or deletes no AWS resources**. That sentence is what lets a user
say yes without stopping to think about blast radius, and a gate whose fix a user
hesitates over is a gate that gets worked around.

### When the install itself is the blocker

A remedy command that times out is not a remedy. On a network where PyPI is slow or
unreachable — which is the common case inside mainland China — name a mirror instead
of letting the user watch a stalled download and conclude the power is broken:

```bash
# pip
./.venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ 'sagemaker>=3.22,<4'

# uv -- --index-url still works but is deprecated in favour of --default-index
uv pip install --default-index https://mirrors.aliyun.com/pypi/simple/ 'sagemaker>=3.22,<4'
```

Either tool also reads it from the environment, which is better when several commands
follow: `PIP_INDEX_URL` for pip, `UV_DEFAULT_INDEX` for uv.

**Do not decide this from the partition.** An `arn:aws-cn:` caller is a hint that the
machine is likely in China, and nothing more — credentials say where the AWS resources
are, not where the shell is. A laptop in Frankfurt can hold Beijing credentials, and a
laptop in Shenzhen can be working in `aws`. Inferring the network from the partition is
the same mistake as inferring the partition from a region name, one layer out. Offer
the mirror when a download is slow or fails, or when the user tells you where they are.

Mirrors serve the same artefacts under the same names, so this changes throughput and
not what gets installed. Verified only that the index answers and carries the package:
`https://mirrors.aliyun.com/pypi/simple/` and its `sagemaker/` path both returned 200,
from a machine outside China — which confirms the URL is right and says nothing about
reachability from inside.

The same shape applies to every gate in this power: state what was declared, what
was found, and the shortest safe path from one to the other.

### v3 canonical paths

Use these, not the shims.

| What | Where |
|---|---|
| Image resolution | `sagemaker.core.image_uris` |
| Processors | `sagemaker.core.{Processor, ScriptProcessor, FrameworkProcessor}` |
| Batch transform | `sagemaker.core.Transformer` |
| Training | `sagemaker.train.ModelTrainer` |
| Training configs | `sagemaker.core.training.configs` |
| Deploy / packaging | `sagemaker.serve.ModelBuilder` |
| Session | `sagemaker.core.helper.session_helper.Session` (re-exported as `sagemaker.train.Session`) |
| Pipeline **primitives** | `sagemaker.core.workflow` — `ConditionEquals`, `ConditionLessThanOrEqualTo`, `JsonGet`, `ParameterInteger`, `ParameterString`, `PropertyFile` |
| Pipeline **steps** | `sagemaker.mlops.workflow` — `Pipeline`, `ProcessingStep`, `TrainingStep`, `TransformStep`, `ConditionStep`, `FailStep` |
| Model object | `sagemaker.core.Model`? **No** — `Transformer` imports from `sagemaker.core`, `Model` does not |

**That split cost a real run seven failed imports, so take the two rows literally.** The
conditions and parameters are in `core`, the steps are in `mlops`, and both are flat modules
with no submodules to reach into. These all raised `ModuleNotFoundError`, in this order:

```
sagemaker.core.workflow.steps          sagemaker.core.workflow.pipeline
sagemaker.core.workflow.condition_step sagemaker.mlops.workflow.pipeline_context
sagemaker.core.model                   sagemaker.core.configs
sagemaker.core.source_code             sagemaker.train  (as an attribute of `sagemaker`)
```

Every one is the shape a v2 habit or a plausible guess produces. **`import sagemaker` then
`sagemaker.train.…` is the trap worth naming**: the top level exports nothing, so that is an
`AttributeError`, not an import error, and it reads like the package is broken. Import the
leaf name directly. When a path is not in the table, list the module rather than guessing —
`python3 -c "import sagemaker.mlops.workflow as w; print(dir(w))"` settles it in one call,
and the run that guessed instead spent seven.

**v3 is not a stable target either.** In 3.22.0, `sagemaker.train.configs` is a
deprecation shim that warns its canonical home is `sagemaker.core.training.configs`
and that the shim will be removed. Import from the canonical path; a shim import
works today and is a silent breakage later.

### Pass an explicit session

```python
import boto3
from sagemaker.train import Session

boto = boto3.Session(profile_name=<profile>, region_name=<region>)
sm_session = Session(boto_session=boto)
```

With `sagemaker_session=None`, v3 builds its own session and logs `No region
provided. Using default region.` throughout. In a multi-partition setup that is a
real hazard: the ambient profile is not reliably inherited, and a call meant for
`cn-north-1` can end up signed for somewhere else.

## The five runtime modes

| Mode | You supply | Cost | Reach for it when |
|---|---|---|---|
| **Built-in algorithm** | data and hyperparameters | the algorithm is fixed | a built-in covers the task — XGBoost for most tabular work |
| **Script mode (BYOS)** | `entry_point`, `source_dir`, `dependencies`, on an AWS framework image | only PyPI packages, or what the image already has | your own algorithm in a supported framework; the common case for DL |
| **Extend a prebuilt image** | a Dockerfile `FROM` an AWS image | an image to maintain | you need a system package or CUDA library the framework image lacks |
| **Custom container (BYOC)** | a full image in ECR | the contract below | an unsupported framework, a compiled binary, or a security requirement the prebuilt images cannot meet |
| **BYOM** | trained artefacts only | no training happens | the model already exists — bought, downloaded, or trained elsewhere |

Choose the least you can get away with. Every step down the list is an image you
own and patch. The single most common over-reach is building a container to add one
`pip` package that script mode's `requirements.txt` would have installed.

In v3, script mode changes shape usefully: a `SourceCode` configuration syncs a
local directory into the job at runtime, so **changing your training script does not
require rebuilding the container**. You still bring an image — yours, an AWS Deep
Learning Container, or a third party's — and the SDK injects the code.

## Do not build the image on the user's machine

**Build with CodeBuild, not with local Docker.** A run reached the point of needing a BYOC
inference image, probed for Docker, and got:

```
$ docker version --format '{{.Server.Version}}'
zsh: command not found: docker          # exit 127
```

That is a **normal machine**, not a broken one. Docker Desktop is a licensed install on a
corporate laptop, a local build is the wrong architecture whenever the laptop is arm64 and
the endpoint is x86, and a multi-GB image pushed from a home connection is slow in a way no
one budgeted for. Requiring it turns "register a model" into an IT ticket.

So the container build is a **cloud** step. Ask the user which they have, and do not assume:

| Route | When | What it costs |
|---|---|---|
| **CodeBuild** | the default — a buildspec, a source zip in S3, `aws codebuild start-build` | build minutes; no local tooling at all |
| `sm-docker` (SageMaker Studio Docker CLI) | inside Studio, where it wraps CodeBuild for you | same, plus a Studio domain |
| Local `docker build` | the user already has Docker **and** the architecture matches | free, and only then |

Two things the buildspec must get right, because both produce an image that builds and then
fails at runtime: `--platform linux/amd64` unless the endpoint is explicitly Graviton, and an
ECR login in `pre_build` (`aws ecr get-login-password | docker login --username AWS
--password-stdin`) against **the partition's own registry host** — `.amazonaws.com.cn` in
China, which is the same never-assemble-a-URI rule as below.

**And a third, in China: configure the package and image sources, or the build times out rather
than failing.** `pypi.org` and Docker Hub are slow to unreachable from inside the partition —
CodeBuild is inside it too. Prefer `public.ecr.aws` for the base image, set `PIP_INDEX_URL`
(and `UV_DEFAULT_INDEX`, which uv reads instead) to a China mirror. The table of sources is in
`ml-planning/references/china-baseline.md`; `FROM python:3.9-slim` with a bare `pip install` is
the shape that stalls.

**If the base image comes from an ECR mirror, `pre_build` needs two logins, not one.** Reading a
base image out of a mirror registry and pushing the result to your own are different registries,
so they are different `get-login-password` calls. One login succeeds and the build then fails on
the other — at pull time for a missing base, at push time for a missing target — which reads like
two unrelated faults.

**Check for Docker before writing a plan that needs it, not after.** `command -v docker` is
one line, and its absence changes the stage's design rather than stopping it.

## The BYOC contract

From the SageMaker developer guide. These are not conventions to prefer; they are
what the platform requires.

**Directory layout inside the container**

```
/opt/ml
 ├── input      job input, including the data channels
 ├── model      training WRITES the final model here; inference READS it from here
 ├── code       your script and its dependencies
 ├── output     job output
 └── failure    failure description
```

**Training.** SageMaker runs a script named `train` inside the container by
default. To use a different entry point, set `ContainerEntrypoint` and
`ContainerArguments` in `AlgorithmSpecification`. The script must write the final
model artefact to `/opt/ml/model` — nothing else is collected.

**Inference.** The container must serve on **port 8080** and answer `POST` to
`/invocations` and to `/ping`. Regular responses must return within **60 seconds**,
streaming responses within **8 minutes**, with a maximum payload of **25 MB**. A
container that does not answer `/ping` never becomes healthy, and that is the
single most common reason a BYOC endpoint fails to come up.

**Use the toolkits rather than reimplementing.** The SageMaker Training Toolkit and
the SageMaker AI Inference Toolkit satisfy these conventions — the directory
locations, the entry point discovery, the serving stack — so use them unless you
have a reason not to. Reimplementing the contract by hand is where the subtle
failures come from.

### A `-slim` base image is missing libraries your wheels assume

`pip install` succeeding proves nothing about whether the library can **load**. Many numeric and
ML wheels link against system shared objects that a slim image does not carry, and the failure
arrives at *import* time inside the job — not at build time, where you would see it.

**`requirements.txt` structurally cannot express this, which is the root cause rather than an
aside.** pip resolves Python distributions; a shared library is an OS package. So the one file
everyone treats as "the dependency list" is blind to an entire class of dependency, and a
dependency list can be complete, correct, reviewed and still not describe what the image needs.

Two things follow, and neither depends on knowing which library.

**Declare the OS packages as well, in a file beside the Python ones** rather than inline in a `RUN`
line where nobody reviews them, with a reason per entry — a reader cannot tell from a package name
what needs it:

```
# system-packages.txt — one line per package, and why it is here
<pkg>        # <which import fails without it, and what it dlopens>
```
```dockerfile
COPY system-packages.txt /tmp/
RUN apt-get update \
    && sed 's/#.*//' /tmp/system-packages.txt | xargs -r apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*
```

**Import every dependency in the build, not just install it.** One `RUN python -c "import a, b, c"`
layer turns a failed inference job into a failed build, which is cheaper by orders of magnitude —
and it catches the next such library without anyone having to know its name in advance. That is the
check to write, because the specific library changes with every framework and the omission does not.

> **Measured instance**, for scale rather than as the rule. An image built `FROM python:3.9-slim`
> installed a gradient-boosting library cleanly, pushed, registered, and failed in a real batch
> transform with `OSError: libgomp.so.1: cannot open shared object file`. The OpenMP runtime is
> missing from slim images and several numeric wheels `dlopen` it; the fix was one
> `apt-get install libgomp1`. Its `requirements.txt` was correct throughout — two revisions, across
> eight Dockerfile revisions and one billed failure.

### Choosing `-slim` is the decision that generates all of the above

That same run wrote **eight Dockerfile revisions** and changed base image four times:

```
12:22  FROM python:3.9-slim
12:30  FROM <account>.dkr.ecr.<region>.amazonaws.com.cn/sagemaker-scikit-learn:1.2-1-cpu-py3
12:39  FROM python:3.9-slim                          ← reverted
12:42  FROM <public-mirror>/python:3.9-slim          ← Docker Hub unreachable
13:50  + ARG PIP_INDEX_URL=<regional mirror>
14:03  + vendored wheels, --no-index --find-links    ← index still not working
15:27  + apt-get install <missing runtime>           ← the transform job had already failed
```

**It had the right answer at 12:30 and abandoned it.** A regional SageMaker or DLC image is inside
the partition, needs no public registry and no mirror, and already carries the numeric runtimes,
because the frameworks it ships need them too. Every line after 12:39 is a consequence of the
revert: the unreachable pull, the mirror, the vendored wheels, and finally a missing system library
three hours later and one billed job downstream.

So treat the base image as the load-bearing choice it is. `FROM python:*-slim` is the
reasonable-looking default that buys a small image and pays for it in the four problems above;
start from a regional AWS image and add what it lacks, which is usually only your own code.

## Resolving the image — never assemble a URI

```python
from sagemaker.core import image_uris

image = image_uris.retrieve(framework="xgboost", region=region, version="1.7-1")
```

The resolver gets the registry account, the region and the endpoint suffix **right
together**, and that pairing is the whole problem. Measured, in the same call:

| Region | Built-in algorithm account | Suffix |
|---|---|---|
| `cn-north-1` | `450853457545` | `amazonaws.com.cn` |
| `cn-northwest-1` | `451049120500` | `amazonaws.com.cn` |
| `us-west-2` | `246618743249` | `amazonaws.com` |

Two traps in that table. The built-in accounts **differ between the two China
regions**, so "the China account" is not a thing. And they are different again from
the Deep Learning Container accounts — `763104351884` globally, `727897471807` in
China. Code that learned one number and reused it is wrong in a way that surfaces
only at image-pull time.

The defect this produces, worth recognising because it is silent until runtime:

```
763104351884.dkr.ecr.cn-northwest-1.amazonaws.com.cn/pytorch-training:2.0.0-cpu-py310
                                    ^^^^^^^^^^^^^^^^ China suffix
^^^^^^^^^^^^ global account
```

The URI was computed in a Lambda and passed straight into `AppSpecification.ImageUri`,
so nothing downstream corrected it — the job simply could not pull its image.

Where no resolver is available (the JavaScript SDK has no equivalent):

1. Keep account and suffix in **one table entry per region**, never two independent
   variables.
2. Resolve at **runtime** from the concrete region, not at CDK synth time where the
   region may still be an unresolved token — a failed lookup would fall back to the
   global account and recreate the bug.
3. Accept an environment-variable override so the image can be pinned by digest or
   repointed at a mirror without a code change.
4. **Assert the pairing in a test**, separately from asserting each region's full
   URI. A test that only checks complete URIs also passes when someone updates both
   strings wrongly at once; a test asserting "the global account never appears with
   the China suffix" does not.

### `py_version` changed in v3

For xgboost and scikit-learn, `py_version="py3"` is now ignored with an
informational log. For pytorch it raises `ValueError: Unsupported Python version:
py3`. Do not carry a v2 call site over unchanged.

## Pin by digest for anything that must be reproducible

A tag is a moving pointer. An image referenced by tag makes rollback a fiction,
because the tag may resolve to different bytes next week. Resolve the tag once,
record the digest, and use the digest in the release candidate — `release-and-serve`
requires `imageDigest` as one leg of its provenance triple for exactly this reason.

## Distributed and accelerator images

For multi-node training, use a training-job distribution configuration rather than
orchestrating nodes yourself.

**In the China partition**, two image families are absent and no configuration
recovers them: **Neuron** images (there are no Trainium or Inferentia instances) and
**`pytorch-smp`** model-parallel images. HyperPod is absent as well. Multi-GPU on a
single instance and multi-instance data-parallel training both work; model-parallel
via `smp` does not. Plan around it rather than discovering it at submit time — see
`ml-planning/references/china-baseline.md`.

## Diagnosing a container that will not start

In order, because the cheap checks eliminate most cases:

1. **Can the image be pulled at all?** Compare the URI's account and suffix against
   the table above. This is the most common failure and it is free to check.
2. **Does the execution role permit the pull?** A cross-account or cross-partition
   ECR repository needs a repository policy, not just an IAM policy.
3. **For inference: does `/ping` answer on 8080?** Run the container locally and
   `curl` it before blaming SageMaker.
4. **For training: did anything reach `/opt/ml/model`?** A job can exit 0 having
   written nothing. `release-and-serve`'s first gate exists for this: success means
   exit code zero **and** the declared artefacts exist.
5. **Read the failure description**, not only the job status. `/opt/ml/failure` and
   the CloudWatch log stream carry the actual error; `FailureReason` on the job is
   often a truncation of it.

A note on where the error appears: a container-internal failure surfaces as
`AlgorithmError` with a Python traceback from inside the image. That is a different
class of problem from a `ValidationException` on the API call — the first means
SageMaker ran your code and it failed, the second means SageMaker never started.
Reading which one you have saves the most time of anything in this section.
