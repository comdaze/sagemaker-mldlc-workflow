---
name: governed-release
description: Takes a registered model package to production through a provenance-pinned, governed release - a release candidate carrying code, data and image digests, a single correlation id re-asserted at every stage, a human approval gate whose decision only one governed component may write, and per-environment deploy with verification. Use when designing or debugging model release, approval, promotion between environments, deployment audit, or reproducibility of a shipped model. Applies in every partition; the approval UI matters most where the console has no Model Registry page.
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

## The release candidate

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

### Reject mutable state by field name, not by review

The candidate is where a mutable field gets smuggled in, and it always arrives
disguised as convenience: *store the approval status here so the UI does not have
to call `DescribeModelPackage`.* Mirroring it is the bug
`dont-rebuild-what-you-can-read` describes, and a schema alone will not stop it —
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

## The correlation id

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

## Gate 1: success means exit code AND artefacts

A job that reports `SUCCEEDED` has told you about itself. Require both:

- the process exited 0, **and**
- the artefacts the contract demands actually exist

This gate exists because a generated-code AutoML runner exited 0 after every one
of its internal iterations had failed. Status said success; there was no model.
Declare the required output files in the contract (a metrics file, exactly one
model directory, a results table) and check for them.

## Gate 2: validate the candidate before anyone can approve it

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

## Gate 3: approval — separate the three roles

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

## Per-environment promotion

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
archived deliberately (see `dont-rebuild-what-you-can-read`); and there is no
config-name comparison, so record in the plan which verification you traded away
and what replaced it. A verification that degraded silently is worse than one that
was never claimed.

Instance type and count come from the contract per environment. There is no
right-sizing service to consult in the China partition, so this is a declared
decision revised by measurement — not a gap some tool fills.

## Artefacts that cannot be deployed

Some registered artefacts have no serving-container contract — a generated-code
AutoML output, for instance. These should be **registered and governed but not
deployable**.

Implement that as a separate approval-only pipeline: source, pin, validate,
manual approval, mark approved — **and no deploy stages at all**, reusing the same
governed mutator code as the main path. Merge both groups in the approvals view so
one surface shows everything awaiting a human. Encoding "not deployable" as the
absence of stages is stronger than a flag someone can override.

## Failures should be artefacts, not log archaeology

Emit a structured diagnostic record on failure — stage, component, status, error,
affected resources, output locations, the correlation id, a timestamp — and surface
it where the human is. "The user should not have to go read S3 to find out why it
failed" is a design requirement, not a nicety, and it is cheap once the record has
a schema.

## Where the console cannot help you

In the AWS China partition the Studio console has **no Model Registry page and no
approval UI**, so the approval surface described here is not a nicer alternative —
it is the only one. Everything above uses plain control-plane APIs, which work
fully in that partition; see `ml-planning/references/china-baseline.md`.
Remember that ARNs are `arn:aws-cn:` there — write partition-agnostic ARN patterns
(`arn:(aws|aws-cn|aws-us-gov):`) rather than pinning one partition, or the platform
cannot later move.
