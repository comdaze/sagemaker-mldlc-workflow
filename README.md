# Kiro Power: SageMaker MLDLC workflow

An AI-coding-driven machine-learning development life cycle (MLDLC) for Amazon
SageMaker — a planner that composes a run from named stages, plus one skill per stage
that can also be called on its own.

**In scope:** traditional machine learning and deep learning. Tabular regression and
classification, time-series forecasting, and models trained in a framework or a
custom container.

**Not in scope:** large-model fine-tuning. That is a different track with its own
contracts, and AWS ships an official skill for it (see [Neighbours](#neighbours)).

**Verified baseline:** the AWS China partition. The content is general — these
skills teach SageMaker, not China — but every capability claim carries a China
verdict, and the capability matrix records how each one was established. The reason
for that emphasis is not regional: a workflow that quietly does not run where its
user is, is worse than one that says so.

Version 0.1.0. Eight skills cover all sixteen stages. See [Skills](#skills).

## The flow

```mermaid
flowchart TB
    subgraph PLAN["ml-planning"]
        S1["1 Frame the problem<br/>target, granularity, and the prediction time"]
        S2["2 Environment readiness<br/>caller ARN decides the partition<br/>installed SDK version is a gate, not a note"]
        PRESET{"Scope preset · six named<br/>full-lifecycle · retrain-existing · batch-serving<br/>realtime-serving · inference-only · data-prep-only"}
        PMD[("PLAN.md<br/>PARTITION + SDK + PRESET + LAST_DONE<br/>nine per-task states, checked by plan-lint.py<br/>next.py dispatches · report.py is the only writer")]
        S1 --> S2 --> PRESET --> PMD
    end

    PMD --> S3

    S3["3 Register the dataset"]
    S4["4 Leakage guard<br/>knowable at prediction time?<br/>behavioural screen refuses a feature<br/>that nearly IS the target"]
    S5["5 Data processing"]
    S6["6 Baseline first<br/>naive scores before any model"]
    S7["7 Training"]
    S8["8 Tuning"]
    S9["9 Evaluation"]
    S10{"10 Quality gate<br/>fails closed: no registration"}
    S11["11 Model registration<br/>PendingManualApproval"]
    S12["12 Governed release<br/>provenance triple, three gates,<br/>approval split three ways"]
    S13["13 Batch inference"]
    S14["14 Real-time inference"]
    S15["15 Monitoring<br/>and what not to self-build"]
    S16(["16 Retraining<br/>back to 3 or 5"])

    S3 --> S4 --> S5 --> S6 --> S7 --> S8 --> S9 --> S10
    S10 -- passes --> S11 --> S12
    S10 -- fails --> S16
    S12 --> S13
    S12 --> S14
    S13 --> S15
    S14 --> S15
    S15 --> S16

    RT{{"runtime-and-containers<br/>cross-cutting: built-in / BYOS / extended / BYOC / BYOM<br/>image resolution · SDK version gate"}}
    RT -.-> S5
    RT -.-> S7
    RT -.-> S13
    RT -.-> S14

    PIPE{{"ml-planning composes<br/>stages 5 through 11 into one Pipeline object"}}
    PIPE -.-> S5
    PIPE -.-> S11

    style S10 fill:#FF9900,color:#232F3E
    style S4 fill:#FF9900,color:#232F3E
    style RT fill:#00A1C9,color:#ffffff
    style PIPE fill:#00A1C9,color:#ffffff
    style PMD fill:#00A1C9,color:#ffffff
```

Ordering is a prerequisite chain: each stage's output is the next one's required
input. The dotted boxes are cross-cutting rather than stages.

## Calling one stage on its own

Stages 3, 4, 5, 6, 7, 8, 9, 12, 13, 14 and 15 are each invocable alone — "just run a
batch transform", "just build the processing job", "just screen these inputs". Naming
the stage, its prerequisites, and a one-task `PLAN.md` is the whole ceremony. A skill
covering four stages is still invoked for just one of them.

Composing stages into a `Pipeline` and calling one alone are **two uses of the same
content**, not two bodies of it. `ml-planning` composes, because deciding the stage
sequence and compiling it are one decision in two forms; each stage's own skill teaches
how to write that step.

## Skills

| Skill | Stages | Standalone | Ships a script |
|---|---|---|---|
| `ml-planning` | 1, 2, presets, `PLAN.md`, pipeline composition | orchestrator | `plan-lint.py` |
| `data-pipeline` | 3, 5 | ✔ | `contract-check.py` |
| `leakage-guard` | 4 | ✔ | |
| `train-and-tune` | 6, 7, 8 | ✔ | `training-check.py` |
| `evaluate-and-gate` | 9, 10 | ✔ | `quality-gate.py` |
| `release-and-serve` | 11, 12, 13, 14 | ✔ | |
| `monitor-and-retrain` | 15, 16 | ✔ | |
| `runtime-and-containers` | cross-cutting: 5, 7, 9, 13, 14 | ✔ | |

**Sixteen stages, eight skills, deliberately not one-to-one.** An earlier version made
them one-to-one and projected about 4,600 lines of skill body; the authoring guidance caps
a body at 500 lines because it loads on every trigger, and CRISP-DM has grouped a
comparable task list under six phases since 1996. The stage numbers survived the
consolidation unchanged, because sixteen stages map onto the six standard phases without
remainder. `docs/DESIGN.md` records the measurements.

Every stage marked standalone is still callable alone — a skill covering four stages is
still invoked for just batch inference, and the scope presets name stages rather than
skills.

## SDK v3 is the main line, and it is still moving

Measured on 2026-09-16, against PyPI and a real job in `cn-north-1`:

| Fact | Evidence |
|---|---|
| v3 is the active line | 3.22.0 released 2026-09-15; 3.0 released 2025-11-20; 22 minor releases in ten months |
| v2 is frozen, not retired | last new minor 2.257.0 on 2026-02-03; patches only since. No official end-of-support date found |
| `pip install sagemaker` gives v3 | so using v2 means pinning `<3` deliberately |
| **v3 is a package rewrite** | the top level exports nothing; packages are `ai_registry`, `core`, `lineage`, `mlops`, `serve`, `train`. Seven common v2 imports all fail |
| v3 works against the China control plane | an `ml.m5.large` XGBoost job via `ModelTrainer` in `cn-north-1` reached `Completed`, produced `model.tar.gz`, reported `train:rmse` 0.30005, billed 134 s |

So the power declares `spec.runtime.sdk` in the contract and **gates on it** rather
than shipping two sets of idioms. One gate is cheap and its refusal is informative;
two templates would double the content and drift.

Three traps this cost real time to find, all now written down in
`runtime-and-containers`: never hand-assemble an ARN (a role with an IAM path does
not exist at `role/<name>`, and SageMaker's error reads like a trust-policy
problem); pass an explicit `Session(boto_session=...)`; and `py_version="py3"` is
ignored for xgboost and sklearn but raises for pytorch.

Registry accounts are not one table — the built-in-algorithm accounts differ
**between the two China regions** (`450853457545` in Beijing, `451049120500` in
Ningxia) and again from the Deep Learning Container accounts. Resolve with
`sagemaker.core.image_uris.retrieve`; never assemble a URI.

## What actually holds, and what is only advice

Nothing here can stop an agent that has not read it. So this power classifies its
own constraints rather than presenting them as equally binding:

| Class | What it means | Examples |
|---|---|---|
| **Refusal** | honouring it produces code that declines to proceed, and a test can prove the refusal fires | the quality gate does not register a failing model · a bound that does not follow from its own `derivedFrom` · the leakage screen refuses a correlation above its declared bound · `report.py` refuses `[S]` on an `ALWAYS` stage and `[x]` with no artefact on disk · the SDK version gate stops on a mismatch · publish rejects an empty or `"null"` version id · `plan-lint.py` on `PLAN.md` |
| **Accounting** | it cannot refuse, but a missing decision becomes visible | `PARTITION` / `SDK` / `PRESET` / `LAST_DONE` in the plan · `features.assumed[].reason` and the measurement beside it · `[S]` tasks carrying `skipped: <why>` · `failOnRefusalSuppressed` in the gate report · `useSpot` recorded as `false` rather than left absent · off-dispatch reports counted in the ledger · a substitution recorded under "Constraints traded away" |
| **Advice** | nothing checks it; it holds while someone remembers | "prefer the least runtime mode you can get away with" · the five questions in `monitor-and-retrain` · this README staying in step with the skills |

**Three cost decisions are refusals rather than advice, and that is deliberate.** The tuning
**Eight decisions are the user's, and the tooling refuses to make any of them.** Each was found by
the same audit question — *does a required field admit only one of several legitimate options?* —
because a rule shaped that way decides while looking like it enforces.

| The decision | Recorded as | Why it cannot be the tool's |
|---|---|---|
| The **margin** over the strongest baseline | `marginChosenBy: user` | It *is* the ship / do-not-ship line. 1%, 5% and 20% are all defensible and they are different businesses |
| The **primary metric** | `metricChosenBy: user` | MAE, RMSE and a daily P95 do not rank the same candidates the same way, so choosing it chooses the model |
| The **algorithm** | `algorithmChosenBy: user` + `algorithmAlternatives` | It settles what the model can express, what the serving stack is, and who maintains it |
| The **data scope** | `scopeChosenBy: user`, with a reason per exclusion | Leaving data out changes what the evaluation is evidence *about* |
| Where the **split** falls | `splitChosenBy: user` | A test window too short yields a number the gate then treats as authoritative |
| The tuning **method** | `methodChosenBy: user` | A fixed list and an AMT search differ by orders of magnitude in cost |
| The AMT **strategy** | `strategyChosenBy: user` | Bayesian cannot scale parallelism, Grid is categorical-only, Hyperband needs an iterative algorithm |
| The **compute** behind a search | `computeChosenBy: user` | It runs `maxJobs` times over, which is a budget rather than a setting |

Two of them arrived by finding the defect in this power's own code. `training-check.py` demanded a
`candidates` list, which an AMT search cannot supply — so the only shape that could pass was the one
a validation run picked without asking, and the tool was steering rather than merely silent. That is
also why `algorithmAlternatives` must name something *other* than the choice: a menu with one item
makes "the user agreed" true and hollow.

**The asymmetry is deliberate.** Recording an instance type is required everywhere; the *signature*
is required only where the choice multiplies or sets a threshold. Demanding one per job is the blunt
instrument that gets worked around, and a user cannot usefully reason about the fortieth instance
decision in a row. Two more from the audit stayed accounting rather than refusals — fixed
hyperparameters, and which baselines to compute — because defaults are defensible there and tuning
and the consistency check are the respective remedies.

**Spending money is a second authorisation, separate from approving the plan.** Stages 5–9, 13 and
14 are declared `billable`: until the plan carries `COMPUTE: <type> x<count>, spot=<bool>,
maxRuntimeMin=<int>, authorisedBy=user @ <ISO>`, `next.py` dispatches `authorise-compute` instead of
the work and `report.py` refuses the state change. One line covers the run. A run reached stage 5,
created a real Pipeline and started an `ml.m5.large` job with a 60-minute ceiling — approving a plan
that says "processing runs as a ProcessingStep" is not approving that.

**Stage 10 is deliberately not on that list, and it used to be.** The quality gate is arithmetic
over two JSON files; marking it billable made the loop demand a compute authorisation before the
stage could be reported at all. A run met exactly that, was told by its user that no instance was
needed, and re-designed the stage as a `ConditionStep` reading stage 9's metric with
`quality-gate.py` as a local verifier. It was right, and it had to argue with `stages.toml` to get
there — the declaration was wrong, not the run.

**Between refusal and accounting sits `APPROVED:`.** No execution work is dispatched until the
plan carries `APPROVED: "<the user's words>" @ <ISO>`, and an agent can write that line itself —
so it is not proof of consent and is not offered as any. What it is: a quoted sentence attributed
to the user, in the file they are reading, which makes inventing one a visible lie rather than an
invisible omission. Inside the loop it is a refusal, because nothing else gets issued. The rule
"present the plan for approval" had been in `ml-planning` as prose since the skill existed, and
held exactly as well as prose does: a run wrote its plan and, in the same turn, delivered
contracts, feature code, a trained model, an evaluation and a compiled Pipeline.

**Between refusal and accounting sits one mechanism that is neither.** `next.py` cannot stop
an agent editing `PLAN.md` by hand — no script can — so instead it makes the edit **not
count**: the ledger digests the plan's state, and a plan changed by any other route stops
earning directives until it is reconciled. Tampering is not blocked; it costs the agent its
own next instruction. That only works on a run that uses the loop at all, which is why the
row below still governs everything.

**And one class below all three: whether a skill loads at all.** Measured at roughly
one plain request in two, with the same model and description (see
[Install](#importing-is-not-enough--add-a-steering-line)). Nothing inside a skill
affects it — content is only reachable once the skill is in context, so every row of
the table above is conditional on activation. A project steering line is the only
thing observed to make it deterministic, which is why that is an install step rather
than a tip.

The third column is the honest one. It comes from an audit of this power's
predecessor on a real end-to-end run: **constraints written as a refusal survived
into the artefacts; constraints that stayed prose either held by luck or went
missing without a trace.** In that run a metric from the default list went
unimplemented and nothing caught it — which is why omissions must now be declared
rather than merely avoided.

## The agent does not decide when it is finished

A validation run compiled a real SageMaker Pipeline, wrote 48 artefacts and 8 code files,
and read the shipped scripts unprompted. Its plan then stopped at task 14 with **stages 14,
15 and 16 absent** — not skipped, not deferred, gone, with contiguous numbering so nothing
looked wrong. The same run skipped stage 13, batch inference, the 96 predictions the request
was for.

Neither is a knowledge failure, which is why neither is fixed by more text. Both are
failures of a loop whose exit condition the agent owns: nothing anywhere said *you are not
finished*, because being finished was its own call.

```bash
S=skills/ml-planning/scripts
python3 $S/next.py PLAN.md --artifacts artifacts/     # one directive
# ... do exactly that, then ...
python3 $S/report.py --task 5 --state x --artifact artifacts/processing-report.json
python3 $S/next.py PLAN.md --artifacts artifacts/     # the next one
```

`next.py` dispatches in a fixed order — repair a plan missing a stage, finish local work in
hand, poll a submitted execution, wait on an unanswered question, execute the
lowest-numbered ready task, or report complete. Prerequisites come from `stages.toml`'s
`requires`, never from task numbering: stage 13 depends on 10 and not 12, and no rule based
on integers knows that. Against that run's own plan it answers `repair-plan: stages missing
14, 15, 16`.

`report.py` is the only sanctioned writer of task state. It refuses what the stage declares
must not happen, and it **writes, re-lints, and reverts its own write** if the result would
fail — so the plan is lint-clean after every report, not just after the ones someone
remembered to check. Working out of dispatch order is *recorded rather than blocked*, because
refusing every off-dispatch step would make the tool something to work around, and a bypassed
control protects nothing.

**Two things this does not do.** It cannot prevent a hand edit of `PLAN.md`; it makes one not
count. And it does nothing at all if nothing calls it — see the activation row above, which
governs every claim on this page.

For scale: `aidlc-workflows` owns sequencing and gate status outright, with state transitions
restricted to its own tools, across 51 TypeScript files and 96,134 lines. This is about 600
lines of Python holding two of the same properties — one directive at a time, one writer of
state — by digest and convention rather than by architecture.

## The measurement that shaped stage 6

A validation run reported MAE 18.8 against a quality gate of 130. Every structural
check passed. The gate passed.

One column, used **directly as the prediction with no model at all**, scored 14.18 —
better than the trained model — correlating 0.975 with a target whose standard
deviation was 232. It was a settlement quantity derived from realized outcomes, and
its name suggested a price contracted in advance.

```
naive: predict the mean            MAE 182.71
naive: same period yesterday       MAE 137.06
the suspect column, used as-is     MAE  14.18   corr 0.975   ← the leak
the trained model                  MAE  18.76   ← worse than the leak alone
```

Two things follow, and both are now structural. `train-and-tune` makes the naive
scores a required output, so an implausible result is visible on sight.
`leakage-guard`'s behavioural screen is a **refusal**, because sorting features by
name is a hypothesis and names lie.

## No MCP server, on purpose

Three reasons, in increasing order of weight.

1. **The dedicated server does not help here.**
   `awslabs.sagemaker-ai-mcp-server` is at 1.1.0 and covers SageMaker HyperPod only
   — the one capability the China partition lacks entirely. Zero usable tools for
   this power's verified baseline.
2. **That line has an official successor.** `awslabs/mcp` now states the Agent
   Toolkit for AWS "is the successor to the MCP servers, plugins, and skills
   available on AWS Labs" and that "over time, the most useful projects here will
   move into Agent Toolkit for AWS". Welding an awslabs MCP into `mcp.json` bets on
   a line its own maintainers call superseded.
3. **An MCP does not solve this power's problem.** MCP servers wrap AWS *APIs*; the
   hard part here is generating *correct code*. Seven dead imports, an ARN path, a
   session that does not inherit its profile, and a config shim mid-move are all
   code-generation failures. No MCP prevents any of them; a skill naming the
   canonical paths and a version gate do.

**Optional companion, not bundled:** the AWS Knowledge MCP Server at
`https://knowledge-mcp.global.api.aws` needs no AWS credentials and answers a real
MCP handshake. It addresses exactly the failure mode this README is full of — the
SDK moved and a model's training data did not. It is not in `mcp.json` because
`mcp.json` cannot load conditionally per region, so bundling would push it onto
every user. Whether it is reachable from a given network inside China was **not**
established: one probe succeeded from one machine whose egress path is not
characterised, against a CloudFront-fronted endpoint. Re-run the handshake from your
own network before depending on it.

## Install

Kiro → Powers panel → **Add Custom Power** → **Import power from GitHub** (paste the
repository URL) or **Import power from a folder**.

Requires Kiro IDE, Kiro on the web, or Kiro CLI v3+. Choose **Vibe** when Kiro asks
— skills trigger reliably in vibe mode and less consistently in spec mode.

Kiro **copies** a power into `~/.kiro/powers/installed/` at import time, so changes
to your clone need a re-import to take effect.

### Importing is not enough — pick one of the deterministic ways in

Skill activation is a model judgement, not a rule. Measured on one machine with one
model (GPT-5.6 Terra, vibe mode, identical prompt), a plain request activated
`ml-planning` **twice out of four times**; on the two misses a global skill in
`~/.kiro/skills/` won the match instead. Rewording the description did not fix it —
the run immediately after the description was widened still missed.

Four ways in, and three of them do not depend on that judgement:

| Way in | Setup | Per use | Reliability |
|---|---|---|---|
| **Type `/ml-planning`** | none | four keystrokes | by name, no matching |
| **Project steering file** | one file, once | none | **measured 3/3** |
| **Try power** button | none | one click per session | engages the power, then asks for an overview |
| Plain request | none | none | **about 1 in 2** |

**`/ml-planning` is the cheapest.** Importing the power registers all eight skills as
slash commands in the composer, each with its description, so typing `/ml` narrows to
one — none of the thirteen global skills on the test machine starts with those
letters, and none collides with a name in this power. It addresses the failure
directly: the same crowded match pool that loses the coin flip has no effect when the
skill is called by name.

Its limit is that the user has to know which skill to call. For the planner that is
fine — `ml-planning` is the answer to "I do not know which one" — but picking among
five puts a decision on the person who should not have to make it. So the slash
command is the zero-setup entry, and the steering file is what removes even those four
keystrokes.

```markdown
---
inclusion: always
---

Machine-learning work in this repository goes through the `sagemaker-mldlc-workflow`
power. Start with its `ml-planning` skill and follow the workflow it defines.
```

Put that at `.kiro/steering/ml-workflow.md`, or copy the one this power ships:

```bash
mkdir -p .kiro/steering
cp ~/.kiro/powers/installed/sagemaker-mldlc-workflow/steering/getting-started.md \
   .kiro/steering/ml-workflow.md
```

Steering files are loaded unconditionally rather than matched, which is why this
removes the coin flip. This is not a quirk of this power — AWS's own Agent Toolkit
documents the same requirement for its skills. Kiro also reads `~/.kiro/steering/`,
listed as **Global** in its steering panel, so one file there covers every project at
the cost of applying to projects that are not about machine learning.

**The `steering/` directory this power ships does not fire on its own.** It was
measured: with `POWER.md` and `steering/getting-started.md` both installed, an
activation reported that it received no `POWER.md` body and no steering guide, and
fell back to reading the skills. `POWER.md` supplies the power page — the title, the
`by` line, the keyword tags, the description — and `steering/` is the copy-source for
the command above. Neither replaces the project file. The twelve official powers that
ship `steering/` are pure-legacy, with no `plugin.json`; whether that is what makes
the difference was not tested, because finding out costs the cross-harness
portability the plugin format buys.

**Two manifests, two audiences.** The power page renders `POWER.md`; the activation
result an agent receives carries `plugin.json`'s description instead — established by
the fact that the activation text contains a sentence only `plugin.json` has and omits
the setup line only `POWER.md` has. So page text reaches the human and skill text
reaches the agent, which is why the setup prompt also lives in `ml-planning` itself.

## Validate

```bash
python3 scripts/validate.py            # uses the pinned schema in schemas/
python3 scripts/validate.py --refresh  # re-fetch the schema first
```

Validates `plugin.json` against the [Agent Plugins 1.0.0](https://agent-plugins.org/)
schema, checks that every skill has a `SKILL.md` whose frontmatter `name` matches its
directory, cross-checks `stages.toml` against `skills/` and against `ml-planning`'s own
stage table, **runs the two committed fixture suites**, and **fails when a git-ignored file
is sitting in the tree**. A schema it cannot load is a hard failure, not a warning — a
validator reporting success while skipping its main check is worse than no validator.

The regression step is there because the previous fixtures were not. They lived in a scratch
directory and are gone, so the checks they covered have no reproducible evidence behind them.
Worse, over one day the linter grew from 7 rules to 11 and **each new rule was verified only
to be capable of erroring** — never that a complete, honest plan could still satisfy all of
them. So `trial-plan-lint.py` now generates a well-formed plan for every preset in
`stages.toml` and requires all of them to pass, then breaks one condition at a time and
asserts that *the intended check* fires. It also reads the label inventory out of
`plan-lint.py`'s own source, so **a check added without a fixture is reported as uncovered
and fails the run**.

```bash
python3 scripts/trial-plan-lint.py                            # 15 checks, 4 presets
python3 scripts/trial-control.py skills/ml-planning/scripts    # 13 control-layer cases
python3 scripts/trial-fixes.py                                # 12 cases for three fixes
```

Breaking a refusal on purpose was checked: with `check_skippability` stubbed out,
`validate.py` fails and names the fixture that stopped holding.

That last check exists because `.gitignore` protects the repository and not the artefact:
**"Import power from a folder" copies the working directory**, so a file ignored because
it is machine-local gets packaged anyway. One machine's editor settings shipped inside an
installed copy of this power twice before the check existed. Run `validate.py` before
importing and the rule holds without anyone remembering it.

Seven scripts ship with the power. Five belong to a skill; `next.py` and `report.py` are the control layer. Each corresponds to rules in its skill body, and
each exists as a script rather than as prose for one reason: **a rule that can refuse
holds, and a rule that stays prose holds only while someone remembers it.**

```bash
python3 skills/ml-planning/scripts/next.py PLAN.md --artifacts artifacts/
python3 skills/ml-planning/scripts/report.py --task 5 --state x \
        --artifact artifacts/processing-report.json
python3 skills/ml-planning/scripts/plan-lint.py PLAN.md
python3 skills/data-pipeline/scripts/contract-check.py \
        contracts/dataset-manifest.json artifacts/processing-report.json
python3 skills/leakage-guard/scripts/leakage-screen.py \
        data.csv contracts/feature-policy.json --out artifacts/leakage-audit.json
python3 skills/train-and-tune/scripts/training-check.py \
        artifacts/baseline-report.json artifacts/training-report.json \
        artifacts/tuning-report.json
python3 skills/evaluate-and-gate/scripts/quality-gate.py \
        contracts/quality-gate-contract.json artifacts/evaluation-report.json \
        --out artifacts/quality-gate-report.json
```

| Script | Refuses on |
|---|---|
| `next.py` | **it decides the order, so the agent does not** — no `PRESET`, a plan `plan-lint.py` rejects, a ledger digest that disagrees with the plan, or unfinished work with nothing dispatchable. A preset stage with no task at all gets a `repair-plan` directive instead of a violation list |
| `report.py` | **the only sanctioned writer of task state** — `[S]` on an `ALWAYS` stage, `[S]` on a `DELIVERABLE` without `waived-by: user`, `[x]` when the stage's declared artefact is not on disk, `[>]` on a stage whose mode is not `pipeline`, any state change downstream of a stage whose `holds` is `hard`, and any execution-stage report before the plan carries an `APPROVED:` line. It also **consults the gate rather than trusting one ran**: where the artefact records its own verdict it reads the declared field, and where the gate is a separate program it runs it. Writes, re-lints, and reverts its own write if the result would fail |
| `plan-lint.py` | numbering, one state marker per task, at most one `[-]`, no `[x]` above an unsettled task, `[S]` outside its stage's execution class, `[!]` without `refused:` and a live `blocks:`, `[>]` without `execution:` or on an inline stage, a `PRESET` whose stages the tasks do not cover, `LAST_DONE` disagreeing with the highest `[x]`, and a `Skill:` that does not match the owner its `Stage:` implies |
| `contract-check.py` | a dataset identity with a hole, a `versionId` that is null or the string `"null"`, an unverified read-back, an absent completeness assertion, partition counts that do not reconcile, a group key in two partitions, overlapping boundaries, a transform fitted outside the training partition, outputs with no digest |
| `leakage-screen.py` | **it computes on the data, standard library only, so it runs inside a processing job** — a correlation or single-input AUC above the declared bound, or a candidate whose error used directly as the prediction lands within the declared margin of the quality bound. That last one is exemptable, and only that one: naming an input in `assumedKnownAtPredictionTime` **with a reason** turns its direct-prediction refusal into a recorded assumption carrying the measurement, because the bound is derived from the strongest baseline and a baseline is usually a column of the data — so any good column scores near it. A correlation of 0.999 is not exemptable, and the probe is passed an empty exemption map so no contract can disarm the self-check. Exits 2 rather than reporting when it cannot be trusted: no bounds declared, or **its own built-in probe did not fire** |
| `training-check.py` | no baselines, the weaker baseline named as strongest, a bound not derived from it, an image pinned by tag, a test channel in training or tuning, no model artefact, a winner not recorded as fixed before test access · **and the decisions that cost money**: a tuning `method` the user did not choose, an AMT `strategy` outside the four the API names, `Grid` over a non-categorical range or with a job count that is not its combination count, `Hyperband` without `iterativeAlgorithm: true`, an unrecorded instance type or count, and `useSpot` left absent — because absent means on-demand, so the expensive option would be chosen by omission |
| `quality-gate.py` | **it computes the verdict rather than checking one** — a bound that does not follow from the artefact its own `derivedFrom` names, a contract that cannot be shown to predate the predictions, a model worse than a recorded baseline, an unexplained missing metric, no independent recomputation, or a hand-written verdict that disagrees. Exits non-zero on `REFUSED`, so a failing gate stops a step instead of producing a document someone has to read |

Every refusal above was verified by breaking a fixture one field at a time. Two were
additionally checked against reality: the gate was run against a real end-to-end run's
numbers and independently reached the same `REFUSED`, and the leakage screen was run on
synthetic data with a planted leak — refusing it at correlation 0.9999 and a
direct-prediction MAE of 0.37 against a target whose standard deviation was 34 — and on the
same data with the bounds set so that nothing could fire, where its own probe stopped the
run.

## Neighbours

- **AWS's own `aws-ai-ml` skill**, in the Agent Toolkit for AWS registry — model
  selection, fine-tuning (SFT, DPO, RLVR, RLAIF), evaluation, endpoint diagnostics,
  PySDK v3 usage. States it is not for Ground Truth, Feature Store or general AWS
  infrastructure. Its shape is large-model customisation, so it does not cover the
  traditional-ML and DL training lifecycle this power is about.
- **`kiro-power-sagemaker-ai`** — packages AWS's upstream `sagemaker-ai` plugin, 19
  skills, also shaped for large-model fine-tuning.
- **`kiro-power-sagemaker-tabular-mlops`** — this power's predecessor, kept rather
  than migrated: it carries a complete end-to-end validation record and serves as
  the reference implementation and regression comparison. `release-and-serve` and
  `monitor-and-retrain` come from it; the first differs from its source
  only in two cross-references, the second is byte-identical.

## Provenance and scope

The patterns here are drawn from a production forecasting platform running in the
China (Ningxia) Region, plus measurements taken read-only against a China account in
September 2026 with `us-west-2` probed identically as a control. Verdicts taken from
AWS documentation are labelled as such; `docs/DESIGN.md` records which claims were
measured, which were run end to end, and which were only documented.

**Only patterns travel.** No customer identity, account, bucket, resource name,
internal URL or business logic appears. Where a specific value is given it is either
published by AWS (the registry accounts) or a measured error string quoted as
returned by the API.

Deliberately **out of scope**, stated rather than improvised: probabilistic and
quantile forecasting; data generation and labelling (SageMaker Ground Truth is closed
to new customers in every partition); endpoint right-sizing, for which there is no
sizing service in the China partition and no substitute worth pretending about;
adversarial label leakage and privacy-motivated feature exclusion.

## License

Apache-2.0.
