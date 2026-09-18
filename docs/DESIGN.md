# Design record

Why this power is shaped the way it is, and what was measured rather than assumed.

Everything under "Measured" was verified by running it on 2026-09-16 against
`cn-north-1` and PyPI. Everything under "Assumed" is explicitly not verified.
That split is the point of this file: a later reader must be able to tell which
claims to re-check.

## What this is

An AI-coding-driven ML development workflow for Amazon SageMaker, in the sense
that `aidlc-workflows` is one for software development: a planner that composes a
workflow from named stages, plus one skill per stage that can also be invoked on
its own.

**In scope:** traditional machine learning and deep learning — tabular
regression and classification, time-series forecasting, and DL trained in a
framework or custom container.

**Out of scope:** large-model fine-tuning. That is a different track with its own
contracts, and AWS already ships an official skill for it (see "Neighbours").

**Verified baseline:** the AWS China partition. The content is general — the
skills teach SageMaker, not China — but every capability claim carries a China
verdict, and the capability matrix lives in
`skills/ml-planning/references/china-baseline.md`. The reason for that emphasis
is not patriotism about a region; it is that a workflow which quietly does not
run where its user is, is worse than one that says so.

## What may travel into a skill, and what may not

This power is validated against real projects, and that creates a specific way for
it to fail: a skill written from one project's code teaches the shape of that
project while looking like general guidance. The user of a churn model or an image
classifier then receives rules fitted to a market-price dataset, with nothing in
the text to warn them.

So there is one line, and it is narrow enough to apply without judgement:

> **A measurement may be the ARGUMENT for a constraint. It may never be the
> CONTENT of one.**

| Admissible | Not admissible |
|---|---|
| "One column used directly as the prediction scored MAE 14.18 against a target whose standard deviation was 232" — why the leakage screen is a refusal | A column name, a schema, a table of allowed and forbidden fields from some dataset |
| "A gate of 130 passed a model at 18.8 while a single column scored 14.2" — why stage 6 exists | "Split by trading day, 96 rows per day" — one dataset's grain presented as a rule |
| "A run attributed ten stages to skills that do not own them" — a workflow defect any dataset would produce | A domain unit, a market, a sensor layout, a business threshold |
| A measured platform fact: registry accounts, an SDK import that fails, a container port | An instance type or hyperparameter that suited one model |

The test for the first column is whether a reader could reconstruct the project
from it. The test for the second is whether the rule survives changing the
problem.

**A validation run's role is to be a test case, not a source.** It shows that a
rule is needed, and it shows whether a rule that exists actually fires. It does not
supply the rule's text. The nine stages a real 17-task run implemented from first
principles produced 2,683 lines of working code, in the China partition, against a
live control plane — and none of it is skill material, because all of it answers
"how do I process this dataset" rather than "how does a SageMaker processing job
work".

### The three-domain check

Every skill is checked against three problems that share nothing but SageMaker:

1. **Tabular classification** — churn or fraud, no time axis, class imbalance.
2. **Time-series forecasting** — a horizon, a prediction time, chronological splits.
3. **Unstructured deep learning** — images or text, a framework container, GPU
   instances, no feature columns at all.

A rule that cannot be stated for all three is a rule borrowed from one project. What
happens to it next is the subject of the next section, and it is not deletion.

This is how the audit that let two skills be copied byte-for-byte was framed, and
it caught a real leak: 37 lines of forecasting vocabulary in the planner, against 2
and 1 in the two that travelled unchanged.

### Where domain material goes: `SKILL.md` decides, `references/` explains

Failing the three-domain check is not grounds for deleting content. Correlation
thresholds are the right leakage test for a scalar input and meaningless for an
image; publication-time-versus-observation-time is essential for a temporal source
and absent from a churn table. All of it is true and useful. It just is not
universal, and a skill body that presents it as universal misleads two readers out
of three.

The skill format already answers this. Quoting the authoring guidance this power
follows:

> **Avoid duplication**: Information should live in either SKILL.md or references
> files, not both. **Prefer references files** for detailed information unless it's
> truly core to the skill […] Keep only essential procedural instructions and
> workflow guidance in SKILL.md; **move detailed reference material, schemas, and
> examples to references files.**

So each skill is two layers, and the split is a specific one:

| Layer | Holds | Loaded |
|---|---|---|
| `SKILL.md` | What the rule is. Whether it is a refusal. What happens when it fires. The decision procedure. | Every time the skill triggers |
| `references/<domain>.md` | How to measure it in one modality. Statistics, thresholds, probe constructions, per-domain examples. | Only when the agent judges it needed |

The dividing question is **"what do I do" versus "how do I measure it"**. The first
is the same in all three domains and belongs in the body. The second differs per
modality and belongs in a reference.

**A refusal never moves to a reference.** This is the failure mode the split is
designed to prevent, and it was proposed in a real audit: because correlation does
not apply to images, generalise the rule to "unusually predictive inputs require
provenance review". That trades this power's core mechanism for tidiness. A
constraint written as a refusal survives into the artefacts; the same constraint
written as a review suggestion holds by luck. So the body keeps *this is a refusal
and it does not proceed*, and the reference supplies *and here is the statistic for
your modality*.

Two consequences worth stating, because both are improvements rather than costs.
Domain material can be **more** detailed once it is out of the body, since it no
longer competes for the context every invocation pays for. And a reader working on
one of the other two domains never sees it — which is the actual goal, since the
harm was never that the content existed.

`ml-planning/references/china-baseline.md` is the pattern already in use: the
capability matrix lives there, the body carries one instruction to read it before
proposing anything. Where a reference grows past roughly ten thousand words, put
grep patterns in the body rather than expecting it to be read whole.

## The stage catalogue

Ordering is a prerequisite chain: each stage's output is the next one's required
input. Stages marked **standalone** are the ones a user calls on their own.

**Sixteen stages, eight skills.** The two are deliberately not one-to-one — see
"Why the stages and the skills are not one-to-one" below.

| # | Stage | Owning skill | SageMaker surface | Standalone |
|---|---|---|---|---|
| 1 | Frame the problem | `ml-planning` | — | |
| 2 | Environment readiness | `ml-planning` | partition, quotas, capability probe | |
| 3 | Register the dataset | `data-pipeline` | versioned S3 identity | ✔ |
| 4 | Leakage guard | `leakage-guard` | — | ✔ |
| 5 | Data processing | `data-pipeline` | `ProcessingStep`, Feature Store | ✔ |
| 6 | Baseline first | `train-and-tune` | — | ✔ |
| 7 | Training | `train-and-tune` | `TrainingStep`: built-in / framework / custom container / distributed | ✔ |
| 8 | Tuning | `train-and-tune` | `TuningStep` | ✔ |
| 9 | Evaluation | `evaluate-and-gate` | `ProcessingStep`, same image as training | ✔ |
| 10 | Quality gate | `evaluate-and-gate` | `ConditionStep` | |
| 11 | Model registration | `release-and-serve` | `RegisterModel`, `PendingManualApproval` | |
| 12 | Governed release | `release-and-serve` | approval + provenance | ✔ |
| 13 | Batch inference | `release-and-serve` | `TransformStep` / Batch Transform | ✔ |
| 14 | Real-time inference | `release-and-serve` | endpoint, data capture | ✔ |
| 15 | Monitoring | `monitor-and-retrain` | drift, and what not to self-build | ✔ |
| 16 | Retraining | `monitor-and-retrain` | back to 3 or 5 | |

Two cross-cutting skills own no numbered stage because they apply at several:
`runtime-and-containers` (what runs your code, at stages 5, 7, 9, 13, 14) and
pipeline composition, which belongs to `ml-planning` — the planner decides the stage
sequence, and composing that sequence into a `Pipeline` object is the same decision
expressed as code rather than as `PLAN.md`.

The machine-readable form is `skills/ml-planning/references/stages.toml`, and it is
declarative rather than a list. Each stage declares five things:

```toml
[stages.13]
name = "Batch inference"
owner = "release-and-serve"
execution = "DELIVERABLE"    # ALWAYS | DELIVERABLE | CONDITIONAL
requires = [10, 12]
mode = "pipeline"            # inline | pipeline
produces = "batch-predictions/"
```

It replaced two flat two-column files, `stage-catalogue.txt` and `stage-artefacts.txt`.
The format comes from reading how `aidlc-workflows` actually enforces its flow: its 33
stage files declare `execution`, `requires_stage`, `lead_agent`, `mode`, `consumes` and
`produces` in frontmatter, and its engine reads that graph instead of the agent
re-deriving it in prose. `tomllib` has been in the standard library since Python 3.11, so
this costs no dependency — the same reason `leakage-screen.py` computes its statistics by
hand.

**Four rules were previously inexpressible, and each traces to an observed failure.**
`execution` classes, because `[S]` used to require a reason and never check what it said.
`requires`, because ordering compared task numbers and stage 13 depends on 10 and not 12.
`mode`, because a run put five stages in flight with no state meaning "submitted, awaiting
a remote result". And `PRESET` coverage, because a plan declared sixteen stages and wrote
fourteen tasks, with contiguous numbering so nothing looked wrong.

**The validator then caught two contradictions in this design**, which is the part worth
recording. Declaring stages 4, 6, 7, 9 and 10 as `ALWAYS` while keeping three presets that
exclude them made those presets unsatisfiable — so `ALWAYS` means *not skippable once a
preset includes it*, not *every preset must include it*. A closure rule then found
`retrain-existing` omitting the leakage screen, and `inference-only` serving a model whose
quality gate appeared nowhere. The first was a real gap. The second needed a third concept
rather than a bigger preset: `presets-satisfied-externally` records that the model was
trained and gated in the run that registered it, because expanding the preset to re-run
training would have destroyed the scope in order to satisfy a graph rule.

Stage 6 exists because of a specific failure: in a validation run the pipeline
reported MAE 18.8 against a gate of 130 and every structural check passed, while
a single leaking column used **directly as the prediction** scored MAE 14.2. Had
the flow been required to state the naive baselines first — 182.7 for the mean, 137.1
for the simplest domain-valid reference — the result would have been suspect on sight.

## Why the stages and the skills are not one-to-one

The first version of this design made them one-to-one: sixteen stages, fourteen
skills. That was wrong, and the measurements say so from three directions.

**The format has a cap.** The authoring guidance keeps a `SKILL.md` body under 500
lines because it loads on every trigger. The five phase-one skills average 328 lines,
`ml-planning` is 591 and already over, and fourteen skills at that average projects to
roughly 4,600 lines of body. Eight skills sized to fit projects to about 2,160.

**The established frameworks group.** CRISP-DM has organised roughly two dozen tasks
under six phases since 1996, and CRISP-ML(Q) and Oracle's ML process both keep six.
The stage list is a checklist and a checklist is cheap; a file per checklist item is
not. Sixteen stages map onto the six standard phases without remainder, which is why
the stage numbers survived the consolidation unchanged.

**AI-DLC does more with less text.** The power this one is modelled on ships 39 skills
whose median body is 43 lines, 2,058 in total, covering a larger lifecycle. It manages
that because each stage skill is a thin wrapper that delegates to an engine — the rules
live in code and data, not in prose. That is the architectural lesson, and it is why
each new skill here ships with a script rather than a longer description of one.

## Scope presets

Four named subsets, chosen statically rather than compiled. `aidlc-workflows`
transposes a stage grid from each stage's own `scopes:` tag and validates it with
a drift guard; at four presets and sixteen stages that machinery costs more than
it returns.

| Preset | Stages |
|---|---|
| `full-lifecycle` | 1–16 |
| `retrain-existing` | 3, 5, 7, 9, 10, 11, 12 |
| `inference-only` | 13 or 14 |
| `data-prep-only` | 3, 4, 5 |

## Skills

| Skill | Stages | Body | Standalone | Ships a script |
|---|---|---:|---|---|
| `ml-planning` | 1, 2, presets, `PLAN.md`, pipeline composition | 497 | orchestrator | `plan-lint.py`, `next.py`, `report.py` |
| `release-and-serve` | 11, 12, 13, 14 | 369 | ✔ | |
| `runtime-and-containers` | cross-cutting: 5, 7, 9, 13, 14 | 309 | ✔ | |
| `leakage-guard` | 4 | 303 | ✔ | `leakage-screen.py` |
| `monitor-and-retrain` | 15, 16 | 213 | ✔ | |
| `train-and-tune` | 6, 7, 8 | 187 | ✔ | `training-check.py` |
| `evaluate-and-gate` | 9, 10 | 184 | ✔ | `quality-gate.py` |
| `data-pipeline` | 3, 5 | 165 | ✔ | `contract-check.py` |

**Measured, not projected: 2,227 lines of body across eight files, every one inside the
cap.** The fourteen-skill version projected 4,600. `ml-planning` is the only file that has
had to be pushed back under the line, four times — and the fourth time was the lesson:
shaving sentences kept landing 2 to 16 lines over, so whole sections moved to `references/`
instead. Nibbling at a body that is 40 lines too long is how a body ends up 3 lines too long
repeatedly.

Two properties of the earlier design are preserved and worth stating, because a
consolidation is where they get lost. Every stage marked standalone is still callable
alone: a skill covering four stages is still invoked for just batch inference, and the
scope presets still name stages rather than skills. And composing stages into a
`Pipeline` is still two uses of the same content rather than two bodies of it — that
was `sagemaker-pipeline`'s reason for existing, and folding it into `ml-planning`
keeps the property while removing the file.

Two costs came due. `monitor-and-retrain` **was** byte-identical to its source in
`kiro-power-sagemaker-tabular-mlops`, which was deliberate: it made the predecessor a
regression comparison. Absorbing stage 16 ended that, knowingly. (`release-and-serve` had
already diverged by 48 lines when it gained a `gitCommit` fallback, so the property was gone
there first.) And the skills carry 49 cross-references to each other by name; every one
pointing at a renamed skill had to move with it, which `validate.py` does not check — only
the `stages.toml` cross-check and the reference-pointer check are mechanical.

## What was built, and what each decision cost

The roadmap this section used to hold is spent: all eight skills exist and every body is
inside the cap. What replaces it is the record of the trades, because a design document
that only lists intentions is the half nobody can check later.

| Decision | What it bought | What it cost |
|---|---|---|
| Sixteen stages, eight skills | ~2,160 lines of body instead of ~4,600; every body inside the 500-line cap | attribution is derived rather than declared, so a stage without `Stage: N` is outside every rule |
| `stages.toml` over two flat lists | four rules that could not be written down before | a third file format in the power, and a plan must now declare `PRESET` |
| Refusal over prose, everywhere | 111 checking refusals plus 30 computed ones, all independent of which model is driving | five scripts to maintain, and each new rule needs a fixture or the build fails |
| `next.py` / `report.py` | the agent no longer decides whether work remains | a plan edited by hand stops earning directives until reconciled |
| Keeping `kiro-power-sagemaker-tabular-mlops` | a regression comparison with a complete end-to-end record | two powers to keep in step, and `monitor-and-retrain` is no longer byte-identical to its source |
| China partition as the verified baseline | every capability claim carries a verdict someone measured | content reads as more regional than it is, and a reader outside that partition pays attention tax |

### Control inversion: the failure that better instructions could not fix

Round 10 compiled a real SageMaker Pipeline in `cn-north-1`, wrote 48 artefacts and 8 code
files, read the shipped scripts, and ran `leakage-screen.py --help` unprompted. It was not
an unwilling or careless run. Its plan stopped at task 14 and **stages 14, 15 and 16 were
absent** — not skipped, not deferred, gone, with contiguous numbering so nothing looked
wrong. The same run skipped stage 13, batch inference, the 96 predictions the request was
for.

Neither is a knowledge failure. Both are failures of a loop whose exit condition the agent
owns: nothing anywhere said *you are not finished*, because being finished was its own call.
That is why the fix is not more text. `next.py` issues one directive at a time and
`report.py` is the only sanctioned writer of task state. Against round 10's own plan,
`next.py` answers `repair-plan: stages missing 14, 15, 16`.

**What it does not do, recorded because a control that oversells itself is worse than
none.** It cannot stop a hand edit of `PLAN.md` — no script can. Instead the ledger digests
the plan's semantic state, so an edit by any other route stops earning directives until
reconciled: tampering is not blocked, it costs the agent its own next instruction. And it
does nothing if nothing calls it, which is the activation problem, still answered only by a
project steering line.

**Scale, against the thing it borrows from.** AI-DLC owns stage sequencing and gate status
outright, with state transitions restricted to its own tools — an agent calling
`aidlc-state.ts` directly gets a state-guard error — across 51 TypeScript files and 96,134
lines. This is about 600 lines of Python holding two of the same properties, one directive
at a time and one writer of state, by digest and convention rather than by architecture.
Whether convention is enough is the open question of this design, and round 11 is the test.

### What round 11 is meant to settle

Written before the run rather than after it, because a criterion chosen once the result is
in is not a criterion. Each row names what would count as the control layer failing, so a
disappointing run can be told apart from a wrong design.

| Question | Passes if | Fails if |
|---|---|---|
| Does the loop get used at all? | `PLAN.state.json` exists and its directive count roughly matches the task count | no ledger — the run planned and executed without ever asking what was next |
| Is truncation gone? | every stage the `PRESET` names has a task, at the end as well as the start | stages missing from the tail again, which would mean `repair-plan` was issued and ignored |
| Does the deliverable survive? | stage 13 or 14 is `[x]`, or `[S]` with `waived-by: user` | `[S]` on a deliverable with a reason about implementation |
| Is concurrency recorded honestly? | parallel pipeline stages are `[>]` with an `execution:` | five tasks sharing `[R]` again |
| Are the rules satisfiable in practice? | the plan is lint-clean at the end without hand repair | a run that fought the linter, or reported and reverted repeatedly |
| Did bookkeeping cost ML work? | artefact and code counts at least round 10's 48 and 8 | substantially less real work, which would mean the loop taxes the wrong thing |

**The interesting failure is the last row.** Every mechanism added since round 9 makes the
agent account for itself more, and round 10 already showed the trade: ML work rose while
bookkeeping quality fell. If round 11 reverses that — clean books, less built — the control
layer is buying the wrong thing, and the answer is fewer directives per unit of work rather
than better ones.

One measurement is not available and should not be claimed: all rounds so far ran on the
same model. Whether these refusals help a weaker model is reasoned from mechanism — 111
checking refusals plus 30 computed ones are model-independent, while activation is not —
and remains untested.



### A hypothesis worth recording because it was refuted

The activation transcript ends a skill's body mid-word with `… (truncated)`, cut at
character 9,995 of 26,777 — 37% of `ml-planning` shown. That looks exactly like a
10,000-character delivery cap, and it would have been the best available explanation for
every bookkeeping failure in rounds 9 and 10, because the state table, the skip classes and
the linter invocation all sit beyond it. It would also have meant the 500-line cap this
design is built around was the wrong parameter, and that five of eight skills were more than
a third undelivered.

**Two measurements and one test say no.**

The prediction failed first. If truncation drove the round-9-to-round-10 regression, the file
should have been inside the cap at round 9. It was over at *every* version in git history —
21,701 characters at the earliest, 31,014 at the largest. Both rounds saw the same fraction,
so truncation cannot explain a difference between them.

A discriminating measurement narrowed it further. At round 10's version the sixteen-row stage
table sat at offset 8,026–8,129, **inside** the window, while the state table sat at 10,736,
outside. So the run that lost stages 14, 15 and 16 had the list of them in front of it.

Then the direct test settled it. A freshly activated agent, told to read no files, was asked
what `[>]` means and what the three execution classes govern. It answered all six specific
rules correctly — `execution:` naming the run, legal only on a `pipeline`-mode stage, not
terminal, ALWAYS not skippable even by the user, DELIVERABLE needing `waived-by: user`, and
skipping *all* deliverables being refused. Those sit at offsets 12,161 to 13,382, and the
last exists **only** in `SKILL.md` and not in `stages.toml`, so it could not have come from
the data file. The truncation is in the transcript display; the body reaches the agent whole.

**The refutation is worth more than the hypothesis would have been.** Had the cap been real,
the remedy would have been to deliver the text better. It is not real, which means every
bookkeeping failure in round 10 happened with the governing text **present and available**.
An agent that has the rule and does not apply it is the exact case this power was built
around, and it is why the response to those failures was declarative metadata and scripted
refusals rather than clearer prose. Text was never the lever; this is the direct measurement
of that, and it removes a confound from round 11 rather than adding one.

One thing was not established: whether the agent honoured the instruction to read no files.
The balance of evidence is that it did — the answers track the body's wording closely,
including a rule absent from every other file — but compliance was not independently
verified.

### Three faults a validation run found in the checks themselves

**The leakage screen refused good features, and the cause was circular.** The
direct-prediction bound *is* the quality gate; the gate is derived from the strongest
baseline; a baseline is usually a column of the data. So any good column scores near the
bound and got refused for being good. My first move was to implement the documented
`minDirectPredictionMarginPct`, and that was wrong on its own terms — a margin widens the
band and refuses *more*. It is implemented, because a declared band beats a bare comparison
and the reference has specified it all along, but the fix is that tier 2 became exemptable
and only tier 2: naming an input in `assumedKnownAtPredictionTime` **with a reason** turns
the refusal into a recorded assumption carrying the measurement. The statistic cannot tell a
leak from a strong feature; only a claim about when the value is knowable can, and that
claim belongs to whoever signs the contract. Correlation and AUC stay unexemptable, and the
probe is passed an empty exemption map so no contract can disarm the self-check.

**The gate failed the wrong way inside a Pipeline.** A metric missing its bound is a result,
not a crashed job; the successor should be a `ConditionStep` routing on
`registrationAllowed`. Round 10 wrote its own 110-line wrapper to get that, and the wrapper
was right. `--no-fail-on-refusal` now does it, non-zero stays the default because outside a
Pipeline the exit code is what makes a refusal hold, and the suppression is recorded in the
report — without that trace the flag would quietly convert a refusal into advice.

**`declaredAt` could be re-declared after seeing the score.** Round 10 found this in itself:
it rewrote its contract and left the old timestamp, which would have forged the very
evidence the field exists to provide. So the bound must now be reproducible — `derivedFrom`
names the baseline artefact, the baseline within it, and the margin, and the gate redoes the
arithmetic. Widening a bound after the fact breaks a calculation whatever the timestamp
says, because the timestamp is the field an author controls and the baseline's measurements
are not. Stated limit: rewriting the baseline report too defeats this.

### The regression suites, and why the positive half matters more

Over one day the linter grew from 7 rules to 11, and **every new rule was verified only to be
capable of erroring** — never that a complete, honest plan could still satisfy all of them.
Between rounds 9 and 10, ML work rose and bookkeeping quality fell, and a linter that cannot
be satisfied was the obvious suspect nobody had ruled out. The old fixtures lived in a
scratch directory and are gone, so the checks they covered had no reproducible evidence.

`scripts/trial-plan-lint.py` now generates a well-formed plan for **every preset** and
requires all of them to pass, then breaks one condition at a time and asserts that *the
intended* check fires. It reads the label inventory from `plan-lint.py`'s own source, so a
check added without a fixture is reported as uncovered and fails the run.

The positive half paid for itself immediately: all four generated plans were rejected,
because a fresh plan has no `[x]` and `LAST_DONE: 0 @ <timestamp>` is refused — the only
legal value is `none`, which the template had never shown. An agent writing its first plan
had no way to get that right.

Three suites, 45 cases, all inside `validate.py`. Verified by breaking a refusal on purpose:
with `check_skippability` stubbed to return early, `validate.py` fails and names the fixture
that stopped holding.

### What gates each skill

A skill ships when all five hold:

1. **It passes the three-domain check**, and everything that failed the check sits in
   `references/` rather than in the body or the bin.
2. **Its refusals are in `SKILL.md`, not in a reference or a comment**, and each has a
   **committed** fixture that proves it fires. A screen that has never refused anything is
   indistinguishable from a screen with a sign error, and both report PASS. "Committed"
   because the previous fixtures lived in a scratch directory and are gone, which is how
   eleven linter rules came to have no reproducible evidence behind them.
3. **The body is under 500 lines** and carries no statistic, threshold or probe
   construction specific to one modality. Those are the reference layer's job.
4. **`validate.py` passes**, which includes the `stages.toml` cross-check and running all
   three fixture suites — so a refusal that stopped holding fails the build rather than
   waiting for someone to notice.
5. **A well-formed plan can still satisfy every rule.** Added after the fact and it is the
   one that would have caught the real regression: the linter reached eleven rules with
   each verified only to be *capable of erroring*, and none verified to be satisfiable.
   `trial-plan-lint.py` generates a passing plan per preset, so this is now mechanical.

Four of the five are checkable by someone other than the author. The third needs
judgement, and this is the cheap version of it:

```bash
grep -niE 'correlation|AUC|mutual information|ml\.[a-z0-9]+\.[a-z]|rng\.normal|0\.9[0-9]|chronological|timestamp' \
     skills/*/SKILL.md
```

A hit is not automatically a violation — a body may say *a bound exists and crossing it
refuses* without saying what the bound is. But every hit has to be argued for, and most
cannot be. On the three existing skills it returns 4 in `leakage-guard` (the measured
argument, a failure-mode row, a strategy enum), 6 in the skill becoming
`release-and-serve`, 3 in `ml-planning`, 1 in the skill becoming `monitor-and-retrain`,
and 0 in `runtime-and-containers` — which is the only one whose measurement layer
already lives elsewhere. That 0 is the target shape.

**Every new skill is written in two layers from the start.** Writing one flat and
splitting it later is how phase one came to need an audit: the body is the path of
least resistance, so material lands there and stays. The audit found 30 items in one
242-line skill, about ten of them the same defect repeated.

## Runtime modes: BYOS, BYOC, BYOM

`runtime-and-containers` owns the choice, because it is the same decision on
three surfaces (processing, training, inference) and getting it wrong is
expensive on all three.

| Mode | You supply | Cost |
|---|---|---|
| Built-in algorithm | data and hyperparameters | the algorithm is fixed |
| Script mode (BYOS) | `entry_point`, `source_dir`, `dependencies`, on an AWS framework image | only PyPI packages, or what the image already has |
| Extend a prebuilt image | a Dockerfile `FROM` an AWS image | an image to maintain |
| Custom container (BYOC) | a full image in ECR | the SageMaker contract, below |
| BYOM | trained artefacts only | no training |

The BYOC contract, from the SageMaker developer guide: the container directory
layout is `/opt/ml/{input,model,code,output,failure}`; training writes the final
model to `/opt/ml/model`; inference reads the model from `/opt/ml/model` and its
code from `/opt/ml/code`; training runs a script named `train` by default, or the
entry point named in `AlgorithmSpecification`'s `ContainerEntrypoint` /
`ContainerArguments`; an inference container serves on **port 8080** and must
answer `POST /invocations` and `/ping`, within 60 s for a regular response and
8 minutes for a streaming one, with a 25 MB payload ceiling. The SageMaker
Training Toolkit and Inference Toolkit satisfy these conventions for you.

## SDK version: v3 is the line, and it is still moving

### Measured

| Fact | Evidence |
|---|---|
| v3 is the active line | 3.22.0 released 2026-09-15; 3.0 released 2025-11-20; 22 minor releases in ten months |
| v2 is frozen, not retired | last new minor 2.257.0 on 2026-02-03; patches only since, last 2.257.6 on 2026-08-10. No official end-of-support date was found |
| `pip install sagemaker` gives v3 | resolves to 3.22.0 today, so using v2 means pinning `<3` deliberately |
| v3 requires Python | `>=3.10` |
| **v3 is a package rewrite, not an upgrade** | the top level exports nothing; the packages are `ai_registry`, `core`, `lineage`, `mlops`, `serve`, `train` |
| v3 works against the China control plane | a real `ml.m5.large` XGBoost job in `cn-north-1` via `ModelTrainer` reached `Completed`, produced `model.tar.gz`, reported `train:rmse` 0.30005, billed 134 s |

Seven v2 idioms, each tested against 3.22.0, all of which fail:

```
from sagemaker import image_uris                      ImportError
from sagemaker.estimator import Estimator             ModuleNotFoundError
from sagemaker.processing import ScriptProcessor      ModuleNotFoundError
from sagemaker.workflow.pipeline import Pipeline      ModuleNotFoundError
from sagemaker.workflow.steps import ProcessingStep   ModuleNotFoundError
from sagemaker.workflow.condition_step import ...     ModuleNotFoundError
from sagemaker.sklearn.estimator import SKLearn       ModuleNotFoundError
```

`sagemaker.__version__` is also gone — use `importlib.metadata.version`.

New locations: `sagemaker.core.image_uris`; `sagemaker.core.{Processor,
ScriptProcessor, FrameworkProcessor, Transformer}`; `sagemaker.train.ModelTrainer`;
`sagemaker.serve.ModelBuilder`. Pipeline machinery is under
`sagemaker.core.workflow/` and `sagemaker.mlops/workflow/` — `sagemaker.mlops`
does **not** export `Pipeline` at its top level.

**v3 is not a stable target either.** In 3.22.0, `sagemaker.train.configs` is a
deprecation shim that warns the canonical path is
`sagemaker.core.training.configs` and that the shim will be removed. Skills must
use canonical paths.

### Decision

v3 is the main line. The consequence is a **version gate**, not a pair of
templates: `spec.runtime.sdk` in the contract, checked at stage 2 against
`importlib.metadata.version("sagemaker")`, failing hard on a mismatch. Two full
sets of idioms would double the content and drift; one gate is cheap and its
refusal is informative.

### Three traps this cost us to find

- **Never hand-assemble an ARN.** `aws iam list-roles` returns a name without its
  IAM path. A role at `role/service-role/<name>` does not exist at
  `role/<name>`, and SageMaker's error — "Could not assume role … ensure that
  the role exists and allows principal `sagemaker.amazonaws.com`" — reads like a
  trust-policy problem. All four candidate roles had clean trust policies, which
  sent the investigation the wrong way twice. Read `Role.Arn` from `get-role`.
  This is the general form of the rule about never assembling an image URI.
- **Pass an explicit session.** With `sagemaker_session=None`, `ModelTrainer`
  builds its own and logs `No region provided. Using default region.` throughout.
  In a multi-partition setup that is a real hazard: construct
  `Session(boto_session=...)` and pass it.
- **`py_version` changed.** For xgboost and sklearn, `py_version="py3"` is now
  ignored with an informational log; for pytorch it raises
  `ValueError: Unsupported Python version: py3`.

### Registry accounts are not one table

Resolved by `sagemaker.core.image_uris.retrieve`, which is why it must never be
hand-assembled:

| Region | Built-in algorithm account |
|---|---|
| `cn-north-1` | `450853457545` |
| `cn-northwest-1` | `451049120500` |
| `us-west-2` | `246618743249` |

These are distinct from the Deep Learning Container accounts (`763104351884`
globally, `727897471807` in China), and the built-in accounts **differ between
the two China regions** — so "the China account" is not a thing.

## No MCP server, on purpose

Three reasons, in increasing order of how much they matter.

1. **The dedicated server does not help here.**
   `awslabs.sagemaker-ai-mcp-server` is at 1.1.0 and covers SageMaker HyperPod
   only, which is the one capability the China partition lacks entirely. Zero
   usable tools for this power's verified baseline.
2. **That whole line has an official successor.** `awslabs/mcp` now states that
   the Agent Toolkit for AWS "is the successor to the MCP servers, plugins, and
   skills available on AWS Labs" and that "over time, the most useful projects
   here will move into Agent Toolkit for AWS". Welding an awslabs MCP into
   `mcp.json` is betting on a line its own maintainers call superseded.
3. **An MCP does not solve this power's problem.** MCP servers wrap AWS *APIs*;
   the hard part here is generating *correct code*. Seven dead imports, an ARN
   path, a session that does not inherit its profile, and a config shim mid-move
   are all code-generation failures. No MCP prevents any of them. A skill that
   names the canonical paths and a version gate do.

### On the Agent Toolkit for AWS

Went GA on 2026-05-06 as the AWS-supported path forward, bundling the AWS MCP
Server (also GA) with curated skills across three plugins — `aws-core`,
`aws-agents`, `aws-data-analytics` — and working with Kiro, Claude Code, Cursor
and Codex. Its MCP server exposes `call_aws` over 15,000+ AWS API operations
using the caller's own IAM credentials, plus `search_documentation` and
`read_documentation`, and supports IAM condition keys so agent actions can be
scoped by a standard IAM policy.

Two things follow. It may become the distribution channel that matters more than
a power registry, which is worth watching. And its managed endpoint is
`https://aws-mcp.us-east-1.api.aws/mcp` — reachable (an unauthenticated `GET`
returns 405, so the host is up and wants `POST`) but requiring credentials that a
China-partition principal cannot sign for that endpoint. So it does not replace
this power for the verified baseline.

### AWS Knowledge MCP: reachable, and worth recommending as optional

`https://knowledge-mcp.global.api.aws` needs no AWS credentials and answers a
real MCP handshake:

```
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-03-26",
 "capabilities":{"tools":{"listChanged":false}},
 "serverInfo":{"name":"AWSKnowledgeMCP","version":"1.0.0"}}}
```

DNS 31 ms, connect 197 ms. It addresses precisely the failure this document is
full of — the SDK moved and a model's training data did not.

**Assumed, not measured:** that this holds from a customer network inside China.
The probe ran from one machine whose egress path is not characterised here, and
the endpoint is CloudFront-fronted. Anyone depending on it should re-run the
handshake from their own network. It is therefore documented as an optional
companion, not bundled in `mcp.json` — which also cannot load conditionally per
region, so bundling would push it onto every user.

## Neighbours

- **`kiro-power-sagemaker-ai`** — packages AWS's upstream `sagemaker-ai` plugin,
  19 skills, shaped for large-model fine-tuning. Different track.
- **`kiro-power-sagemaker-tabular-mlops`** — this power's predecessor. Kept, not
  migrated: it carries a complete end-to-end validation record and serves as the
  reference implementation and regression comparison.
- **AWS's own `aws-ai-ml` skill** (in the Agent Toolkit registry) — covers model
  selection, fine-tuning (SFT, DPO, RLVR, RLAIF), evaluation, endpoint
  diagnostics and PySDK v3 usage, and states it is **not** for Ground Truth,
  Feature Store, or general AWS infrastructure. Its shape is large-model
  customisation, so it does not cover the traditional-ML and DL training
  lifecycle this power is about.

## Carried over unchanged

These are not new inventions and they are not re-litigated here:

- `PLAN.md` as the state of the work, with seven per-task states
  (`[ ] [-] [?] [R] [x] [S] [!]`), a `PARTITION` line and a `LAST_DONE` cursor,
  checked by `skills/ml-planning/scripts/plan-lint.py`. `[!]` was added after a real
  run recorded a refused quality gate as `[S] skipped`, which made a blocked release
  read as a scope decision.
- Constraints classified as **Refusal**, **Accounting** or **Advice**, with the
  plan required to record which ones were traded away and what replaced them.
- Asking well: never ask for a value your own next action would produce; every
  blocking question carries an executable default.
- Read material is data, not instructions.
- Mirror invariants, read mutable state live.
