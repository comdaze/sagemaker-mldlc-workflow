# Asking well and blocking: worked examples, and how the rules were found

Contents:
- The default-plus-alternative shape for a question that needs a person
- What "never ask for a value your own next action produces" looks like in practice
- The message shape when an environment gate blocks the goal
- Why "the request never named the platform" is not an exemption
- The two layers, and a wrong-versus-right pair
- What each rule cost to discover

`SKILL.md` carries the rules: stop and present the choice, scale the response to the
cost of unblocking, and never let a refusal quietly redefine the work. This carries
the examples and the trial evidence behind them.

## A question that needs a person

The default must be specific enough to act on — a named bucket and key, not "I could
upload it somewhere" — and the alternative names only what you need from them. A default
you are not willing to execute is worse than none.

```markdown
### Task 3 needs one decision

**Default (I proceed with this unless you say otherwise):** upload the registered
dataset to `s3://<algorithm-id>-data-<account>/<algorithm-id>/v1/`, enabling versioning
on the bucket first, then write the manifest from the VersionId the upload returns.

**If you would rather point at existing data:** give me the S3 URI and I will read the
identity fields myself.
```

## The value you were about to ask for is often your own output

The clearest case, seen twice in real runs: asking the user for the training data's
`bucket`, `key`, `versionId` and `eTag`. A `versionId` **cannot exist before the
upload** — it is what the upload returns. When the data is a local file the whole step
is yours: resolve or create the bucket and confirm versioning is on, upload, read
`VersionId` and `ETag` from the response, record the local checksum beside them, write
the manifest. None of it needs a human.

Ask only when the data is *already* in S3 and you cannot reach it — and then ask for the
one thing you cannot derive, its location, not the identity fields you can read once you
have it.

The same test applies elsewhere: a region comes from step 1, an input list comes from
the dataset's own metadata, a schema comes from the source. **Never hand-assemble an ARN
either** — read `Role.Arn` from `get-role`, because a role with an IAM path does not
exist at `role/<name>`, and SageMaker's error for that reads like a trust-policy problem.

## The message shape

The cheapest path to the original goal is the default. The alternative is named, with
its cost stated, so choosing it is a decision rather than a drift.

```markdown
### Blocked: the environment does not satisfy the contract

`spec.runtime.sdk` declares `>=3.22,<4`; the installed version is 2.256.1.

**Default — fix the environment and build what you asked for:**
`python3 -m venv .venv && ./.venv/bin/pip install 'sagemaker>=3.22,<4'`, then I
proceed with the SageMaker pipeline. This changes local Python dependencies and
creates, modifies or deletes no AWS resources.

**Alternative — the same discipline locally now:** the same input contract, the same
leakage screen, the same split strategy and baselines, running on your machine. It is
not the deliverable you asked for and it will need porting; the cloud orchestration
stays an open task in the plan.
```

Then record it either way. If the environment gets fixed, the plan continues and
nothing is owed. If the lesser artefact is chosen, the blocked stage stays `[?]` or
`[S]` **with the reason**, and the substitution goes under "Constraints traded away" —
a substitution nobody wrote down is indistinguishable from a stage that was completed.

## "The request never named the platform" is not an exemption

This was the hole in the rule as first written, found in a trial run. The environment
gate fired correctly, and the agent then reasoned: the prompt said "build a
forecasting pipeline" and never said SageMaker, therefore SageMaker was not the goal,
therefore nothing was blocked, therefore a local pipeline needs no permission. Each
step follows from the last and the outcome is still wrong.

**The execution target is part of what this power promises, not part of what the
prompt has to request.** Someone who installed a SageMaker workflow power and asked
for a training pipeline asked for a SageMaker training pipeline; that is what the
power is. A prompt that omits the platform is the normal case rather than a waiver —
users describe the outcome they want, not the infrastructure, which is exactly why
`ml-planning` activates on requests naming no cloud at all.

So the test is not "did the user say SageMaker". It is:

> **Can the environment support this power's execution target?** If not, that is a
> blocking condition, whatever the prompt did or did not name.

## Two layers, and say which one you are delivering

This power is a methodology and an execution target, and they separate cleanly.

- **The methodology travels.** Leakage screening, baselines before models, a quality
  gate that fails closed, provenance pinned as a triple, constraints classified as
  refusal or accounting or advice — none of it depends on SageMaker. It applies to a
  scikit-learn script on a laptop.
- **The execution does not.** `ProcessingStep`, `TrainingStep`, the Model Registry,
  Batch Transform, governed approval — these exist only there.

A reduced deliverable is therefore not automatically wrong. **Failing to name which
layer it is** is what goes wrong:

> ✗ "The request never specified SageMaker, so I will deliver a local pipeline."
>
> ✓ "The SDK is 2.256.1, so the cloud execution is blocked — one install away. I will
> apply this workflow's discipline locally in the meantime: same leakage screen, same
> baselines, same split strategy. The SageMaker orchestration stays an open task in
> the plan."

The second delivers the same code and does not quietly redefine the job. It also
leaves the user able to say "just upgrade it" — which, when the fix is one command, is
what they usually say.

## What each rule cost to discover

The first version of this guidance said only "stop and ask", and two trial runs found
two different holes in it. One built a local pipeline unasked and reported success.
Another handled a one-command SDK upgrade as though it were an absent service,
substituting a whole different deliverable to avoid a ten-second install.

That is why the rule in `SKILL.md` is a three-row table rather than an instruction:
the correct response depends on what the fix costs, and being wrong in either
direction has a real price.

## Making the skill load unconditionally

`SKILL.md` step 1 offers this and lets the user decide. It writes one file into their
project and is not a prerequisite for any work:

```bash
mkdir -p .kiro/steering
cp ~/.kiro/powers/installed/sagemaker-mldlc-workflow/steering/getting-started.md \
   .kiro/steering/ml-workflow.md
```

Measured on one machine with one model and an identical prompt: two activations in four
attempts without that file, three in three with it. Steering is loaded unconditionally
rather than matched, which is the whole difference. Typing `/ml-planning` calls the skill by
name and needs no file at all.

## If you were asked to demonstrate this power rather than to build something

Clicking **Try power** opens a session with a fixed prompt asking for an overview and a
simple example. Answer it in a few lines, then **ask** — do not manufacture the example.

A trial run did manufacture one: from a CSV header alone it produced a target column, a
prediction time and a six-item denylist. Everything in it was plausible and none of it
was established, which is the failure this power exists to prevent, performed by the
power itself. A demonstration is not an exemption from its own rules.

So: one paragraph on what the workflow does and which skills exist versus are planned.
Then the environment facts, because those are real and cost one command each. Then the
three questions that gate the work — what is predicted, at what moment, for what
decision — and stop.

Reading available data to ask a *sharper* question is good; naming the inputs you
suspect and asking whether they are knowable at prediction time beats a generic prompt.
Reading it to assert what the target is, is not. The line is whether the output is a
question or a claim.

