---
name: "sagemaker-mldlc-workflow"
displayName: "SageMaker MLDLC Workflow"
description: "START HERE: type /ml-planning in the composer, or add a steering file - both below. An AI-coding-driven ML development workflow for Amazon SageMaker: a planner that composes a run from named stages, plus one skill per stage that can also be called on its own. Traditional machine learning and deep learning - tabular regression and classification, time-series forecasting, framework or custom-container training - and deliberately not large-model fine-tuning. The AWS China partition is the verified baseline.   WHY THERE IS A SETUP STEP: skill activation is a model judgement, not a rule, and a plain request engaged the planner in only two of four attempts on the machine this was measured on. Calling it by name with /ml-planning skips the matching entirely and needs no setup. To remove even that, put a one-line steering file in the project - it is loaded unconditionally, and measured three times out of three:   mkdir -p .kiro/steering && cp ~/.kiro/powers/installed/sagemaker-mldlc-workflow/steering/getting-started.md .kiro/steering/ml-workflow.md   Clicking Try power engages this power for a single session instead."
keywords: ["sagemaker", "machine learning", "deep learning", "mlops"]
author: "Sean Yang"
---

# SageMaker MLDLC Workflow

## Overview

A workflow for building machine-learning and deep-learning systems on Amazon
SageMaker, in the sense that an SDLC workflow is one for software: a planner that
composes a run from sixteen named stages, plus one skill per stage that can also be
invoked on its own.

Data processing, training, batch transform and real-time inference are each callable
alone or composed into a single `Pipeline` object. Large-model fine-tuning is
deliberately out of scope — that is a different track with its own contracts, and AWS
ships an official skill for it.

The AWS China partition is the verified baseline. The content is general — these
skills teach SageMaker, not China — but every capability claim carries a China
verdict, and the capability matrix records how each one was established.

## When to Use This Power

- Building or retraining a model on SageMaker, from framing the problem to a governed
  release
- Running one stage alone — "just build the processing job", "just run a batch
  transform", "just screen these features for leakage"
- Deciding what runs your code: a built-in algorithm, script mode, an extended image,
  a custom container, or a model brought as artefacts only
- Reviewing a feature set before training, or when an offline score looks too good
- Working in `cn-north-1` or `cn-northwest-1`, where several capabilities the plan
  might otherwise assume do not exist

## What It Provides

Five skills today, of fourteen planned. `ml-planning` states which do not exist yet,
and the plan linter refuses a task attributed to a missing one.

- `ml-planning` — resolves the partition and the SDK version before code is written,
  names the prediction time, picks a scope preset, and maintains a checkable
  `PLAN.md`
- `leakage-guard` — decides which features are admissible by asking whether each value
  was knowable at prediction time, and refuses a feature that nearly *is* the target
- `runtime-and-containers` — the five runtime modes, the SageMaker container contract,
  image resolution per partition, and the SDK v2/v3 gate
- `governed-release` — provenance pinned as code, data and image digests; three gates
  that check evidence rather than status fields; approval split three ways
- `dont-rebuild-what-you-can-read` — whether a missing managed capability should be
  self-built or read from an API you already have

## Install — importing is not enough

Skill activation is a model judgement, not a rule, and every installed skill competes
for the same match. Measured on one machine with one model and an identical prompt: a
plain request engaged `ml-planning` in **two of four** attempts with the power merely
imported, and in **three of three** once the project carried a steering file.

**The cheapest fix costs nothing to set up.** Importing this power registers its five
skills as slash commands in the composer, so start a run by typing:

```
/ml-planning
```

Typing `/ml` narrows to that one. Calling a skill by name skips matching altogether,
which is exactly the step that loses the coin flip.

Its limit is that you have to know which of the five to call. `ml-planning` is the
answer whenever you do not — it is the planner and decides the rest — but if you would
rather not think about it at all, add one file per project instead:

```bash
mkdir -p .kiro/steering
cp ~/.kiro/powers/installed/sagemaker-mldlc-workflow/steering/getting-started.md \
   .kiro/steering/ml-workflow.md
```

That file is a pointer — it names no partition, no SDK version, no `PLAN.md`, and the
skill still does all the work. Steering is loaded unconditionally rather than matched,
which is why it removes the guesswork. `~/.kiro/steering/` works too, listed as
**Global** in Kiro's steering panel, and covers every project at the cost of applying
to projects that are not about machine learning.

Clicking **Try power** engages this power for a single session, with no file at all.

**The `steering/` directory in this power does not fire by itself.** That was measured
too: with it installed, an activation reported receiving no `POWER.md` body and no
steering guide, and fell back to reading the skills. It is the copy-source for the
command above, not a mechanism.

## Where the detail lives

`README.md` carries the stage map and the SDK v2/v3 findings. `docs/DESIGN.md` records
which capability claims were measured against a real China account, which were run end
to end, and which were only documented.
