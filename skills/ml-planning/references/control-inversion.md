# Control inversion: `next.py` and `report.py`

Two scripts that move one decision out of the agent's hands — **whether there is any work
left** — and leave everything else where it was.

`next.py` says what to do next. `report.py` is the only sanctioned way to write a task's
state back. The agent does the work and says what happened, which is what it is good at,
rather than also deciding when the loop ends, which is where it failed.

## What made this necessary

Two archived runs, same model, same rules available.

Round 10 compiled a real SageMaker Pipeline in `cn-north-1`, wrote 48 artefacts and 8 code
files, read the shipped scripts, and ran `leakage-screen.py --help` unprompted. It was not
an unwilling or careless run. Its plan stopped at task 14, and **stages 14, 15 and 16 were
gone** — not skipped, not deferred, absent, with contiguous numbering so nothing looked
wrong. The same run skipped stage 13, batch inference, the 96 predictions the request was
for, on the grounds that the pipeline definition did not contain a Batch Transform yet.

Neither is a knowledge failure, and neither is fixed by better instructions. They are
failures of a loop whose exit condition the agent owns. Nothing anywhere said *you are not
finished*, because being finished was the agent's own call to make.

`plan-lint.py` can now refuse both after the fact. That is worth having and it is not the
same thing: a violation list tells you the run went wrong, and a directive tells you what to
do. Handed "2 violation(s)", a weaker model has to work out the remedy. Handed
`stages missing: 14, 15, 16`, it has one.

Run against round 10's actual plan, with a `PRESET` line added:

```
DIRECTIVE 1  repair-plan
  stages missing: 14, 15, 16
```

## What it cannot do

Said first, because a control that oversells itself is worse than none.

**It cannot stop an agent editing `PLAN.md` by hand.** No script can. What it does instead is
make the edit not count: the ledger holds a digest of the plan's semantic state, and a plan
that changed by any other route stops earning directives until it is reconciled. Tampering
is not blocked; it costs the agent its own next instruction.

**It cannot help if nothing calls it.** That is the activation problem, measured at roughly
one plain request in two, and a project steering line remains the only answer to it. This
layer is for the run that has engaged and then loses the thread — which, on the evidence of
two archived runs, is the failure that actually happens.

**It is not AI-DLC's engine.** That one owns stage sequencing and gate status outright, with
state transitions restricted to its own tools: an agent calling `aidlc-state.ts` directly
gets a state-guard error. It is 51 TypeScript files and 96,134 lines. This is 600 lines of
Python and holds two of the same properties — one directive at a time, and one writer of
state — by convention plus a digest rather than by architecture.

## The loop

```bash
S=~/.kiro/powers/installed/sagemaker-mldlc-workflow/skills/ml-planning/scripts
python3 $S/next.py PLAN.md --artifacts artifacts/     # what now?
# ... do exactly that one thing ...
python3 $S/report.py --task 5 --state x --artifact artifacts/processing-report.json
python3 $S/next.py PLAN.md --artifacts artifacts/     # what now?
```

`next.py --json` emits the directive as JSON for a caller that would rather parse than read.
`next.py --no-record` shows what would be dispatched without recording it.

## What `next.py` dispatches, in priority order

| Order | Directive | Condition |
|---|---|---|
| 1 | `repair-plan` | a preset stage has no task at all — asked **before** the lint gate, so truncation arrives as work rather than as a violation |
| 2 | `finish` | a task is `[-]`: local work in hand outranks new work |
| 3 | `poll` | a task is `[>]`: a remote execution has not resolved |
| 4 | `await-answer` | a task is `[?]`: a question is outstanding, and only `[~]` may proceed alongside it |
| 5 | `execute` | lowest-numbered unsettled task whose **declared** prerequisites are all settled |
| 6 | `complete` | every stage the preset declares is settled |

Prerequisites come from `stages.toml`'s `requires`, never from task numbering — stage 13
requires 10 and not 12, and no rule based on integers knows that.

### And what it refuses

- no `PLAN.md` — there is no run to advance; use this skill to write one
- no `PRESET` — no declared set of stages to advance through
- `plan-lint.py` refuses the plan — advancing a plan that fails its own checks buries the
  fault under later work
- the ledger digest disagrees with the plan
- unfinished work exists and nothing is dispatchable — it names the blocking chain rather
  than inventing an order the declared graph forbids

Consistency checking is **delegated to `plan-lint.py` by subprocess**, not reimplemented.
One rule set. A second one drifts from the first, and then the two disagree about whether a
plan is valid.

## What `report.py` refuses, and why each one

Every rule comes from `stages.toml`, not from the script:

| Claim | Refused when |
|---|---|
| `[S]` | the stage is `ALWAYS`. Not with a reason, not by the user |
| `[S]` | the stage is `DELIVERABLE` and `--waived-by user` is absent |
| `[S]` | the stage is `CONDITIONAL` and `--reason` is absent |
| `[x]` | the stage declares a `produces` and no given path exists on disk |
| `[>]` | the stage's `mode` is not `pipeline`, or `--execution` is absent |
| `[!]` | `--refused` or `--blocks` is absent |
| `[~]` | `--instead-of` or `--blocked-by` is absent |
| `[?]` | `--asked` is absent |

**`[x]` against the disk is the one worth dwelling on.** A run marked five tasks complete
and its plan passed, because nothing compared the claim to the workspace. A claim of
completion that the workspace does not corroborate is now refused outright.

### It writes, then verifies, then reverts

Every write is followed by `plan-lint.py` on the result, and a write that introduces a
violation is rolled back with the violation quoted. So the plan is lint-clean after every
report — not merely after the reports someone remembered to check. A tool that can leave the
state invalid is a tool that eventually will.

### Working out of order is recorded, not blocked

A report for a task other than the dispatched one is accepted when the graph permits it, and
the ledger counts it:

```
NOTE  directive 4 asked for task 7, and this reports task 9. Allowed by the graph
      and recorded; 2 off-dispatch report(s) so far.
```

This is deliberate, and it follows how this power classifies all its constraints: a
**refusal** is for what must not happen, **accounting** is for what must not happen
silently. Refusing every off-dispatch step would make the tool something to work around, and
a control that gets bypassed protects nothing.

## The ledger

`PLAN.state.json`, beside the plan: directive count, what was last dispatched, the state
digest, every report, and every off-dispatch. Delete it to restart the ledger from wherever
the plan now stands — that is the documented escape hatch, not a workaround.

**The digest covers state, not prose.** Markers, stages, and the numbers a rule reads.
Deliberately excluded: the wording of a reason, a question, or a substitution. Those belong
to whoever wrote them, and improving a sentence must not raise an alarm.

One interaction is worth naming because getting it wrong builds a trap. A `repair-plan`
directive asks the agent to **add tasks**, which is a structural edit that necessarily
changes the digest. So issuing that directive **surrenders** the digest rather than
enforcing it. An engine that demands a change and then calls that change tampering has built
a trap, not a control.

## How it was verified

`scripts/trial-control.py` builds a plan and breaks one condition at a time — 13 cases, each
checked for the *intended* refusal rather than for merely some refusal. Two findings came
out of running it rather than out of writing it:

**The truncation directive was unreachable.** `plan-lint.py` refuses an uncovered preset, so
the lint gate fired first and the agent got a violation list where a directive was the point.
The coverage question now precedes the lint gate.

**The first tampering test proved nothing.** Its hand edit also broke `LAST_DONE`, so lint
caught it and the digest path never ran. The test now makes a lint-clean edit — `[ ]` to
`[-]` — which is the only shape that reaches the check it claims to test.

## `[>]` in full

`[>]`: submitted to a remote executor

`[-]` means you are working on it now, locally, and at most one task may be. That is wrong for
a pipeline: a run submitted five stages genuinely in flight, had no state meaning "submitted,
awaiting a remote result", and wrote `[R]` on all five. **The vocabulary forced a false
record.**

```markdown
5. [>] **Process the data** — execution: arn:aws-cn:sagemaker:cn-north-1:…:pipeline-execution/abc123
   _(Stage: 5 | Skill: data-pipeline)_
```

`[>]` is not capped, because one submission legitimately starts several stages. It requires
`execution:` naming the run — an id that exists can be checked, and one that does not was
invented — and it is only legal on a stage whose declared `mode` is `pipeline`. It is not
terminal: the result has not arrived, so nothing downstream may treat it as settled.

`[?]` and `[R]` are not decoration. `[?]` is where a human approval sits by design;
`[R]` is where a failed quality gate puts you. Written as `[-]`, "someone is working on
this" and "this is blocked on a person" are the same state to a resumed session.

The three header lines are the resume contract: `PARTITION` (`aws`, `aws-cn`,
`aws-us-gov`) so a resumed session reads it instead of assuming the global one, `SDK`
for the resolved version, and `LAST_DONE` as the cursor — the highest `[x]` task and
when it completed, or `none`. On resume, read those three and the first unfinished task
before anything else.

