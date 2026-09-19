---
name: release-and-serve
description: Takes a model from a passed quality gate to serving traffic - registration, a provenance-pinned governed release, batch inference, and real-time endpoints. Covers a release candidate carrying code, data and image digests, a single correlation id re-asserted at every stage, a human approval gate whose decision only one governed component may write, per-environment deploy with verification, and choosing between a batch job and an always-on endpoint. Use when designing or debugging model registration, release, approval, promotion between environments, batch scoring, endpoint deployment, deployment audit, or reproducibility of a shipped model. Applies in every partition; the approval UI matters most where the console has no Model Registry page.
---

# Governed release

Two ideas carry this whole skill:

1. **A release candidate is immutable and self-describing.** Every later stage
   re-asserts the same identity and fails closed when it does not match.
2. **Every gate checks evidence, not a status field.** A status field is
   something a process reports about itself; evidence is something you can
   re-derive.

The second is the transferable part. Three independent gates below apply it, and
each one exists because the naive version failed in production.

## Stage 11 — the release candidate

Emitted by the training pipeline once, then never modified. Required fields:

| Field | Why it is required |
|---|---|
| `releaseId` | Stable handle for this candidate |
| `algorithmId` | Which contract it belongs to |
| `modelPackageArn`, `modelPackageVersion` | What was registered |
| `modelDataUrl` | The artefact |
| **`gitCommit`** | The code that produced it |
| **`dataManifestDigest`** | The data that produced it |
| **`imageDigest`** | The image that produced it |
| `metrics` | The evidence the gate passed on |
| `createdAt` | When |
| `correlationId` | The thread tying every stage together |

The three bold fields are the point. **Code, data and image, pinned together, is
the minimum sufficient condition for reproducibility** — most teams pin only
code, then cannot explain why last month's model does not rebuild. Pin the image
by *digest*, not by a tag: a tag is a moving pointer, so `:latest` makes rollback
a fiction.

### When there is no repository to name a commit from

A real run stalled here. The workspace was not a Git repository, `gitCommit` could
not be established, and this skill offered nothing but the requirement — so the task
recorded `[R]` with two blockers where one of them had a fix that takes seconds.

That violated this power's own rule: a refusal is only complete when it carries the
command that clears it. So say which of these applies, and say it with the command:

```bash
# Preferred: make the code addressable, which is what the field is for.
git init && git add -A && git commit -m "Baseline for release candidate <releaseId>"
git rev-parse HEAD          # this is the gitCommit value

# When a repository is genuinely not wanted -- a scratch analysis, a notebook host:
find scripts contracts -type f | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256
```

The second form is a **content digest of the code bundle**, and it is a legitimate
substitute because it answers the same question — *which code produced this* — from
the bytes instead of from a repository. Record it as `codeBundleDigest` with the
exact command that produced it, set `gitCommit` to `null`, and note the substitution
under the plan's "Constraints traded away". What you lose is history: a commit lets
someone read the diff that introduced a regression, and a digest only tells them the
code differs. That is a real loss and it is the reason the first form is preferred.

Four properties make that command usable as provenance, and all four were measured
rather than assumed: touching every file without editing it leaves the digest
unchanged; editing one byte changes it; renaming a file changes it, because the name
is part of each line; and restoring the name restores the original digest. `LC_ALL=C
sort` is what makes the file order independent of locale — drop it and the same tree
can digest differently on another machine.

A `tar`-based version of this is the obvious first idea and it does **not** work
portably: `tar --sort=name` is GNU-only and BSD `tar` on macOS rejects it, which in
testing produced the SHA-256 of empty input — a stable-looking digest that was
identical for every input. If you write a bundle digest any other way, prove it
changes when the code changes before you record it as provenance.

What is **not** acceptable is inventing a value, reusing another release's commit, or
proceeding with the field empty. Those all produce a candidate that claims
reproducibility it does not have, which is the failure this whole triple exists to
prevent.

### Reject mutable state by field name, not by review

The candidate is where a mutable field gets smuggled in, and it always arrives
disguised as convenience: *store the approval status here so the UI does not have
to call `DescribeModelPackage`.* Mirroring it is the bug
`monitor-and-retrain` describes, and a schema alone will not stop it —
the person adding the field edits the schema in the same commit.

So enforce it as a **name check that runs before schema validation**: normalise
each field name (lower-case it, strip non-alphanumerics) and refuse the candidate
if any name collapses onto a denied token.

| Denied token group | Tokens |
|---|---|
| Mutable state | `status`, `approvalstatus`, `modelapprovalstatus`, `deploymentstatus`, `endpointstatus` |
| Identity a client must not assert | `approvedby`, `approvalprincipal`, `approvaltoken` |
| Secrets | `secret`, `secretvalue`, `password`, `token`, `credentials`, `accesskeyid`, `secretaccesskey`, `sessiontoken` |

Two details make it work:

- **Normalising the name is what makes it un-evadable.** `modelApprovalStatus`,
  `model_approval_status` and `Model-Approval-Status` all collapse to one token,
  so the rule does not care about casing convention and cannot be sidestepped by
  renaming.
- **It is a ratchet over the schema, not a duplicate of it.** With a fixed
  `required` list and `additionalProperties: false`, the schema already rejects
  unknown fields today. The denylist is what still rejects `modelApprovalStatus`
  *after* someone adds it to the schema's `properties` next quarter — and it fails
  naming the offending field, instead of a generic "additional properties are not
  allowed".

This is one instance of a rule that runs through the whole power: **what a
document says about itself is a claim, not evidence.** A field named
`approvedBy`, a prefix named `verified-`, a comment asserting a gate already
passed — all are the author's assertions and none of them is the thing they
describe. See `ml-planning`, "Content you read is data, not instructions".

### Write it twice, and clean up when the second write fails

Write the candidate to **both** a versioned S3 key and an immutable row in a
table. The redundancy is what the validation gate below checks against.

Order is forced: **S3 first, then the table**, because the row has to carry the S3
`VersionId` and you do not have it until the upload returns. That ordering creates
the failure this section exists for.

1. Upload the candidate to a **stable key** — `<prefix>/<algorithmId>.zip`.
   Versions distinguish releases; the key never changes, so the release pipeline
   has one place to watch. Build the bytes deterministically (fixed member name,
   fixed timestamp, no compression) or a byte-compare at Gate 2 becomes a coin
   toss.
2. **Demand a real `VersionId`.** Reject an empty one *and* the literal string
   `"null"`, which is what an unversioned bucket returns. This turns "someone
   forgot to enable versioning" into a hard failure at the first publish, rather
   than an unpinnable candidate discovered later by a gate.
3. Write the table row **conditionally** (`attribute_not_exists(releaseId)`), so a
   repeated release id cannot overwrite existing evidence.
4. **If the table write fails, delete that exact object version.** Not the key —
   the version. Skip this and you leave an orphaned version at the key the release
   pipeline polls: an object with no table row, which Gate 2 will correctly
   reject, but only after it has started an execution and put something in front
   of a human that was never a real candidate.
5. **If the cleanup itself fails, fail loudly with the coordinates** —
   `s3://<bucket>/<key>?versionId=<id>` in the error. It is a manual cleanup now,
   and whoever does it needs the exact version.

One asymmetry worth expecting rather than fighting: the candidate JSON **cannot
contain its own `VersionId`**. You do not know it before the write, and editing
the object afterwards to add it would destroy the immutability the whole scheme
rests on. So the version identity lives outside the bytes — in the table row, and
again in a pin-the-source audit artefact the release pipeline emits — and Gate 2
is what makes the two agree.

## Stages 11–14 — the correlation id

Mint exactly one, from **trusted execution context**, not from anything a caller
supplied:

```
<algorithmId>/training/#{codepipeline.PipelineExecutionId}
```

Then re-assert it at every subsequent stage. This is the substitute for managed
lineage tracking — and it is a deliberate substitute: SageMaker Model Cards and
Lineage are available in the China Regions and still went unused, because a
correlation id carried in job tags and package metadata costs one field and
survives whatever happens to those products.

Carry it into the structured diagnostic record too, so a failure anywhere in the
chain is queryable by the same key.

## Stage 12, gate 1: success means exit code AND artefacts

A job that reports `SUCCEEDED` has told you about itself. Require both:

- the process exited 0, **and**
- the artefacts the contract demands actually exist

This gate exists because a generated-code AutoML runner exited 0 after every one
of its internal iterations had failed. Status said success; there was no model.
Declare the required output files in the contract (a metrics file, exactly one
model directory, a results table) and check for them.

## Stage 12, gate 2: validate the candidate before anyone can approve it

Between "training emitted a candidate" and "a human approves it" the candidate
must be proven unchanged:

1. **Pin the source version.** Record the exact S3 object version of the
   candidate at the moment the release pipeline picks it up.
2. **Byte-compare** the pinned object against the immutable table row.
3. Fail closed on any mismatch. Do not "prefer the newer one" — a mismatch means
   you do not know what you are shipping.

Hand off between the training pipeline and the release pipeline **by artefact,
not by trigger**: training drops an immutable, version-pinned candidate at a
known key, and the release pipeline picks it up. A trigger-based handoff carries
no evidence of what it is about.

## Stage 12, gate 3: approval — separate the three roles

The mistake to avoid is one component that shows the button, decides, and writes
the result. Split it:

| Role | Who | Rule |
|---|---|---|
| **Trigger surface** | The UI | May request an approval decision. Writes nothing. |
| **Gate** | A pipeline manual-approval action | Holds the decision. Nothing bypasses it. |
| **Sole mutator** | One governed function | The only code path permitted to write `ModelApprovalStatus`. |

Three rules make it hold:

- **Re-validate before offering the button.** When listing pending approvals,
  re-check each candidate against the live model package: ARN matches, version
  matches, status is still pending. A stale list invites approving something that
  already moved.
- **Reject client-supplied identity.** If the request body carries an
  `approvedBy`, drop it. The approver is the IAM principal that signed the call,
  full stop. An identity field a client can set is not an audit trail.
- **Make the audit write idempotent** — a two-phase record keyed on the release
  id, so a retried approval cannot produce two conflicting audit rows.

A human comment on the approval is worth keeping, but persist it *beside* the
governed audit record rather than inside it. The audit record's schema is the
contract; free text is not part of it.

## Stage 12 — build the image in the cloud, and grant the role all four families at once

When the candidate needs its own container, **build it with CodeBuild.** Do not ask the user to
install Docker: a run probed for it and got `zsh: command not found: docker`, exit 127, on a
perfectly normal laptop. `runtime-and-containers` carries the route table and the buildspec
details; this is what stage 12 adds.

**The service role's permissions fail one at a time, and each one costs a whole build.** That run
spent six builds, failing in `DOWNLOAD_SOURCE` (`CLIENT_ERROR`), then `BUILD`
(`COMMAND_EXECUTION_ERROR`), then `UPLOAD_ARTIFACTS`, collecting one denial per round:

```
s3:GetObject            denied — cannot read the source zip
s3:GetObjectVersion     denied — the bucket is versioned, so plain GetObject is not enough
logs:CreateLogStream    denied — and this one is why the others were expensive
```

**Grant all four families before the first build**, because discovering them serially is the
whole cost: read the source object *and* its versions, push to ECR (`ecr:GetAuthorizationToken`
is registry-wide and separate from the repository actions), and write CloudWatch logs.

**The logs permission is not optional tooling — without it a failure is unreadable.** CodeBuild
reports `COMMAND_EXECUTION_ERROR` and nothing more; the reason is in the log stream the role was
not allowed to create. A build that fails invisibly is worse than one that fails loudly, and the
fix is a permission, not a retry. Grant `logs:CreateLogGroup`, `logs:CreateLogStream` and
`logs:PutLogEvents` first, then debug.

Two more that were paid for in that run: on a **versioned** bucket `s3:GetObject` alone is a
denial, and in China every ARN is `arn:aws-cn:` — a policy written with `arn:aws:` matches nothing
and the denial does not say why.

**Check the project as well as the role, because there are two independent reasons you cannot read
a build.** That run granted the permission and still saw nothing: the project itself carried

```json
"logsConfig": {"cloudWatchLogs": {"status": "DISABLED"}}
```

Fixing either one alone leaves you blind and the symptom is identical both ways. Confirm with
`codebuild batch-get-projects` before spending a build on a question the logs would have answered.

### The build reads the zip, not your working tree

With an S3 source, `docker build` runs against what was **packaged**, so an edit that is not
re-zipped and re-uploaded does not exist. The characteristic failure:

```
ERROR: failed to solve: failed to compute cache key: failed to calculate checksum
of ref …::"/inference.py": not found
```

That run hit it and had to diff its local `Dockerfile` against the packaged copy to see why. **The
tell is an identical error after a fix that should have changed it** — that is a stale archive, not
a stubborn bug. Regenerate the zip in the same step that submits the build so the two cannot
drift, and make the archive's internal layout match the `COPY` paths rather than adjusting one to
the other by trial.

### A FAILED build may have already pushed the image

The most expensive misreading available here. That run's `post_build` verified its own push with
`aws ecr describe-images`; the role lacked `ecr:DescribeImages`, the command exited 254, and
CodeBuild reported the build `FAILED` — **after the push in `build` had already succeeded.** The
image was in ECR the whole time.

**Take the digest from `docker push`, and do not call `describe-images` at all.** The obvious fix is
to widen the role; the better one is to remove the dependency, which is what that run did. The push
output already contains the digest of what was actually pushed, so reading it back asks ECR a
question you have just been told the answer to — and it is the more authoritative answer, because a
tag can be moved afterwards and a digest cannot.

```yaml
post_build:
  commands:
    # digest of what was pushed, with no ECR read permission required
    - IMAGE_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' "$REPOSITORY_URI:$IMAGE_TAG" | cut -d@ -f2)
    - printf '%s\n' "$IMAGE_DIGEST" > digest.txt
```

**This is also why the model package ends up pinned by digest rather than by tag, and the
constraint arrived from two directions at once.** The provenance triple wants the digest; the denied
`DescribeImages` call meant the digest was the only identifier the build could produce, because
resolving a tag is exactly what that API does. A rule that is independently forced by a permission
boundary is a cheap rule to keep.

**And look in ECR before resubmitting.** A blind retry pushes a second image and leaves two digests
both plausibly "the" one, which is a provenance problem rather than a wasted build.

**What held through that episode, and what did not.** Two hours of build failures produced no
false claim of readiness: the candidate stayed `readyForExecution=false` with `/ping` and CSV
`/invocations` still unexercised. But the model package was registered anyway, and the image in it
could not load its own library — so "not marked ready" and "not used" turned out to be different
things. The section below is that lesson.

### Reference the image by digest, and exercise it before registering

**`repo:tag` is the common form and SageMaker accepts it. Use `repo@sha256:…` anyway** — and
note that the same conclusion arrives from a second direction, which is the section above: with
`ecr:DescribeImages` denied, the digest from `docker push` was the only identifier the build could
produce, because resolving a tag is what that API is for. One run supplied the evidence better than
the argument does. It produced **three digests in one repository inside two hours**: one pushed by a
build that reported `FAILED`, one registered as version 1, and one built to fix a runtime library.
Their tags were `codebuild-source-e6350439` and `codebuild-source-551a1e14` — distinguishable only
by the part nobody reads. A tag reference would not have said which image the package contained, and
version 1 contained the broken one.

**Exercise the container before you register it — and put that in the plan, rather than bolting it
on.** That version 1 was registered, described, and sent to a real batch transform job before
anything discovered the image could not load its own library (`libgomp.so.1`, see
`runtime-and-containers`). The run recorded why: *smoke test was not required by `PLAN.md`*.

**Both halves of that are failures, and they have different remedies.** A check that lives only in
prose does not run — so the smoke test belongs in the plan's stage 11 task at planning time, which
is what this section is for. But when the same run then added a smoke-test build mid-flight, its
user objected, and the user was right: work not in the approved plan is not the run's to insert.
Amend the plan and get that agreed, or do not do it. *Declared beforehand* and *improvised
afterwards* are the same activity with opposite standing.

Two calls answer it before an instance is billed: `/ping` must return 200, and `/invocations` must
accept one real record in the payload format the transform job will send. A container that cannot
import its own dependency fails both in under a second.

## Stage 12 — per-environment promotion

After approval, promote per environment (`dev` → `test` → `prod`) with, for each:

- a deploy step that waits for a **smoke check to pass**, not merely for the
  endpoint to reach `InService`
- a verify step that re-asserts the release identity against what is actually
  deployed
- an extra human gate before the environments that matter, showing the previous
  environment's verified evidence

`InService` is another self-reported status. A useful verification compares the
endpoint's *current config name* against the one the release intended, and reports
a mismatch as a failure reason rather than a success.

### When there is no endpoint

A batch-transform or scheduled-job serving mode has no endpoint, no
`EndpointConfigName` and no `InService` — so the two paragraphs above do not
apply, and the answer is not to skip verification. Declare the serving mode in the
contract (`spec.serving.<env>.mode`) and verify against whatever that mode
actually exposes:

| Serving mode | Wait for | Verify identity by comparing |
|---|---|---|
| Real-time endpoint | a smoke request succeeding | current `EndpointConfigName` against the intended one |
| Batch transform | the output artefacts: the `.out` object exists, its row count equals the input's, and every value is finite | the transform job's model package ARN, image digest and `ModelDataUrl` against the candidate |
| Scheduled job | the run's declared output artefacts existing | the job definition's image digest and model artefact against the candidate |

The invariant across all three is the one worth carrying: **wait for a product,
not for a status, and verify the identity of what is actually deployed rather than
what was requested.** A row count that equals the input's and contains no NaN is a
product; `Completed` is a status.

Two consequences of a batch mode worth stating in the plan rather than
discovering later: it has **no data capture**, so production drift has nowhere to
land until either a real-time endpoint with capture exists or the batch inputs are
archived deliberately (see `monitor-and-retrain`); and there is no
config-name comparison, so record in the plan which verification you traded away
and what replaced it. A verification that degraded silently is worse than one that
was never claimed.

Instance type and count come from the contract per environment. There is no
right-sizing service to consult in the China partition, so this is a declared
decision revised by measurement — not a gap some tool fills.

## Stages 13 and 14 — choosing the serving mode, and not defaulting to it

Registration and release put an approved model somewhere. Serving decides how
predictions actually get made, and the choice belongs in the contract as
`spec.serving.mode` before anything is deployed.

| | Batch inference | Real-time endpoint |
|---|---|---|
| Costs | per job, while it runs | continuously, whether or not anyone calls it |
| Fits | scheduled scoring, a periodic forecast, backfills, anything whose consumer reads a table | a request that must be answered inside a user's wait |
| Freshness | as fresh as the last run | as fresh as the request |
| Gives you free | no capacity to manage | data capture, if enabled |

**Default to batch and make the endpoint justify itself.** An always-on endpoint for a
prediction consumed once a day is the most common avoidable cost in this workflow, and
the cheapest thing to get wrong because nothing fails — it just bills. A trial run
declared a daily batch forecast and skipped the endpoint stage entirely, recording the
reason; that is the shape to copy.

If neither is right — a request arriving unpredictably, at low volume, where minutes are
acceptable — say so rather than picking the closer of the two. That is a real gap in
what SageMaker offers cheaply in the China partition, where Serverless Inference does
not exist.

## Stage 13 — batch inference

The job is not the deliverable; the **verified** output is.

**Verify the model identity before trusting a single prediction.** A batch job takes a
model name, and a model name is mutable. Assert that the model the job actually used
carries the approved candidate's `modelPackageArn`, image digest and `modelDataUrl` —
the same triple registration pinned. A batch run against yesterday's model looks exactly
like a batch run against today's.

**Refuse an incomplete input.** Whatever the prediction unit is — a day of intervals, a
batch of records, a page of documents — declare its expected shape and assert it before
submitting. A run that silently scores 71 of 96 intervals produces a file that opens
cleanly and is wrong in a way no downstream check will catch.

**Refuse an incomplete output, too.** Count the predictions, assert they are finite and
unique on the key, and assert the count matches the input. Then archive the input, the
output, the model identity and the error summary together — see `monitor-and-retrain`,
which needs exactly that record to detect drift without the managed capability.

## Stage 14 — real-time endpoints

Only reached when the contract declares it. Three properties to establish before traffic
arrives:

**The container contract is the same one training used.** Port 8080, `POST /invocations`
and `/ping`, and the timeouts `runtime-and-containers` documents. The frequent surprise
is that an image which trains fine can still fail to serve, because serving exercises a
code path training never runs.

**Data capture is the only cheap way to know what production actually sent you.**
Enable it at deploy time. Retrofitting it means a new endpoint config, and the traffic
you wanted to inspect has already gone.

**Sizing is a declared decision revised by measurement, not a task some skill performs.**
The China partition has no Inference Recommender, so instance type and count are stated
per environment in the contract and changed when measurement says to. Do not present a
guess as a recommendation.

## Stages 11–12 — artefacts that cannot be deployed

Some registered artefacts have no serving-container contract — a generated-code
AutoML output, for instance. These should be **registered and governed but not
deployable**.

Implement that as a separate approval-only pipeline: source, pin, validate,
manual approval, mark approved — **and no deploy stages at all**, reusing the same
governed mutator code as the main path. Merge both groups in the approvals view so
one surface shows everything awaiting a human. Encoding "not deployable" as the
absence of stages is stronger than a flag someone can override.

## Stages 11–14 — failures should be artefacts, not log archaeology

Emit a structured diagnostic record on failure — stage, component, status, error,
affected resources, output locations, the correlation id, a timestamp — and surface
it where the human is. "The user should not have to go read S3 to find out why it
failed" is a design requirement, not a nicety, and it is cheap once the record has
a schema.

## Stages 11–14 — where the console cannot help you

In the AWS China partition the Studio console has **no Model Registry page and no
approval UI**, so the approval surface described here is not a nicer alternative —
it is the only one. Everything above uses plain control-plane APIs, which work
fully in that partition; see `ml-planning/references/china-baseline.md`.
Remember that ARNs are `arn:aws-cn:` there — write partition-agnostic ARN patterns
(`arn:(aws|aws-cn|aws-us-gov):`) rather than pinning one partition, or the platform
cannot later move.
