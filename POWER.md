---
name: "sagemaker-ml-workflow"
displayName: "SageMaker ML Workflow"
description: "An AI-coding-driven ML development workflow for Amazon SageMaker: a planner that composes a run from named stages, plus one skill per stage that can also be called on its own. Traditional machine learning and deep learning - tabular regression and classification, time-series forecasting, framework or custom-container training - and deliberately not large-model fine-tuning. The AWS China partition is the verified baseline."
keywords: ["sagemaker", "machine learning", "deep learning", "mlops", "sagemaker pipeline", "training job", "processing job", "batch transform", "real-time inference", "byoc", "script mode", "data leakage", "model registry", "tabular", "time series forecasting", "cn-north-1", "cn-northwest-1", "中国区"]
author: "Sean Yang"
---

# SageMaker ML Workflow

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

## Getting Started

Read `steering/getting-started.md`, which is short and names the two things that must
happen before any code is generated.

The full argument, including which capability claims were measured against a real
China account and which were only documented, is in `docs/DESIGN.md`.
