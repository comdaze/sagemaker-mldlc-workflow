---
name: data-pipeline
description: Registers a dataset as an immutable, verifiable identity and processes it into training inputs without leaking - covering versioned object identity, a manifest downstream stages verify against, completeness assertions before anything is trusted, splits executed so no fitted transform sees data it should not, and content-addressed outputs. Use when uploading or registering training data, when building a processing job or step, when deciding how to split, or when a downstream stage needs to prove which data it consumed. Applies to tables, time series, and image or text corpora in every partition.
---

# Data pipeline

Two stages, one obligation: **every later stage must be able to prove which data it
used.** Stage 3 gives the data an identity that cannot change under it. Stage 5 turns
that data into training inputs without destroying the guarantee.

`leakage-guard` decides *which* inputs are admissible and *how* to split. This skill
executes that decision and proves it was executed. The two are separate because a
correct policy applied by code that fits a scaler before splitting leaks exactly as
thoroughly as a wrong policy.

**This body decides; the references measure.** Split mechanics differ per modality —
what a group is, what completeness means — and that is in `references/`. Read the one
for your data before writing the processing job.

## Stage 3: register the dataset

### A path is not an identity

`s3://bucket/prefix/data.csv` names a location. The bytes at that location can change,
and nothing in a training job would notice. So registration produces an **identity**:

| Field | What it pins | Where it comes from |
|---|---|---|
| `uri` | where it is | the upload |
| `versionId` | which version of that object | the upload response — only exists if versioning is on |
| `etag` | the remote object's own fingerprint | the upload response |
| `digest` | content hash computed locally | your own read of the bytes |
| `sampleCount` | how many samples it contains | your own count |

**Two refusals.**

**Refuse to register without versioning enabled.** A bucket without versioning has no
`versionId`, so the identity has a hole in exactly the field that makes it immutable.
Enable versioning before the upload, not after — a version id cannot be assigned
retroactively to bytes already there.

**Refuse to register an identity you have not read back.** After uploading, read the
remote object's identity and compare it with what you sent. This is one extra call and
it catches the whole class of "the upload half-worked" failure. A trial run did this and
recorded the comparison; that is the shape to copy.

`digest` and `etag` are both fingerprints and they are not interchangeable. An `etag` is
computed by the remote service and its algorithm changes with multipart upload
thresholds; a `digest` is computed by you, the same way every time. Keep both: `etag`
detects a remote change cheaply, `digest` survives a change of storage.

### The manifest is the artefact, not the upload

Write the identity to `contracts/dataset-manifest.json`. Every later stage reads it and
asserts against it rather than taking a path from a variable. That assertion is what
makes "which data produced this model" answerable six months later.

Do not ask the user for any of these fields. `versionId` **cannot exist before the
upload** — it is what the upload returns. See `ml-planning`'s rule on never asking for a
value your own next action produces.

## Stage 5: process the data

### Assert completeness before trusting anything

Declare what a complete prediction unit looks like, then assert it — before computing a
single feature, and again on the output.

This is the cheapest check in the workflow and the one most often skipped, because
incomplete data does not fail. It produces a file that opens cleanly, trains without
error, and is wrong. A trial run asserted its expected sample count per unit and that
assertion is the only reason a boundary bug surfaced before training rather than after.

What "complete" means is modality-specific and is in the references. What is universal:
**declare the expectation in the contract, assert it in code, and refuse rather than
warn.**

### Execute the split so nothing can see across it

`leakage-guard` chose the strategy. This is the execution, and it has one structural
rule: **every fitted artefact is produced inside the step that also does the split.**

Scalers, imputers, encoders, selectors, vocabularies, tokenisers, embedding models —
each is fitted on the training partition only, inside the fold. Not because fitting on
everything is obviously wrong to a reviewer, but because a transform fitted above the
split is a code path in which leaking is possible, and code paths that make a bug
possible eventually contain it.

The refusal: **refuse to emit processed outputs when any fitted artefact's provenance is
not the training partition.** Record each one with what it was fitted on. "Unverified"
is a legal value and a recorded gap; absent is not.

### Content-address the outputs

Downstream stages need to prove they consumed what they think they consumed, so the
processed outputs get the same treatment as the raw data: a digest per output, recorded
in the processing report beside the input manifest's digest.

This is what lets a training job assert its input, and what lets an evaluation six weeks
later establish that it scored the model that was trained on this data and not a
neighbouring run's.

### Choose where the processing runs, and say why

A managed processing job costs a container start — a minute or two — and buys isolation,
a recorded image digest, and an artefact trail. Local processing costs nothing and buys
none of that.

| Use a processing job when | Local is fine when |
|---|---|
| the output feeds a training job | you are exploring, and will re-run it properly |
| the data does not fit comfortably in memory | the whole dataset is small |
| the transform must be reproducible by someone else | the result is a number you will read once |
| the step belongs in a `Pipeline` | there is no pipeline yet |

Declare the choice in the contract as `spec.processing.mode`. An undeclared choice
becomes "whatever was convenient when it was written", which is not reproducible even
when it happens to be right. `runtime-and-containers` covers what runs inside the job
and the container contract it must satisfy.

## What goes in the contract

```yaml
spec:
  data:
    manifest: contracts/dataset-manifest.json
    completeness:
      unit: <what one prediction unit is>
      expectedPerUnit: <count, or the assertion that defines complete>
  processing:
    mode: job | local
    outputs: [<name and digest per emitted artefact>]
    fittedArtefacts:
      - artefact: <scaler | encoder | tokeniser | embedding | …>
        fittedOn: training-partition-only | unverified
```

## Check it mechanically

```bash
LINT=$(ls ~/.kiro/powers/installed/*/skills/data-pipeline/scripts/contract-check.py \
       ./skills/data-pipeline/scripts/contract-check.py 2>/dev/null | head -1)
python3 "$LINT" contracts/dataset-manifest.json artifacts/processing-report.json
```

It refuses on a manifest missing an identity field, a `versionId` that is null or the
string `"null"`, partition sample counts that do not sum to the input count, group keys
appearing in more than one partition, overlapping split boundaries, a fitted artefact
with no recorded provenance, and a completeness assertion that is absent rather than
merely failed. Run it before any training job reads the outputs.

## Prove the refusals fire

Each refusal above gets a test, for the reason `leakage-guard` gives: a check that has
never refused anything is indistinguishable from a check with a sign error, and both
report PASS. The cheapest set is to run `contract-check.py` against a deliberately
broken manifest — one field removed at a time — and assert it exits non-zero for each.

## References

- `references/splitting-by-modality.md` — what a group is, what completeness means, and
  the split mechanics for tables, time series, and image or text corpora.
- `leakage-guard` — which inputs are admissible, and which split strategy to use.
- `runtime-and-containers` — what runs inside a processing job.
