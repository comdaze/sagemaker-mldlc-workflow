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
| Pipeline machinery | `sagemaker.core.workflow/` and `sagemaker.mlops/workflow/` — note `sagemaker.mlops` does **not** export `Pipeline` at its top level |

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
record the digest, and use the digest in the release candidate — `governed-release`
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
   written nothing. `governed-release`'s first gate exists for this: success means
   exit code zero **and** the declared artefacts exist.
5. **Read the failure description**, not only the job status. `/opt/ml/failure` and
   the CloudWatch log stream carry the actual error; `FailureReason` on the job is
   often a truncation of it.

A note on where the error appears: a container-internal failure surfaces as
`AlgorithmError` with a Python traceback from inside the image. That is a different
class of problem from a `ValidationException` on the API call — the first means
SageMaker ran your code and it failed, the second means SageMaker never started.
Reading which one you have saves the most time of anything in this section.
