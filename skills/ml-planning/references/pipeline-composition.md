# Composing the stages into a Pipeline

Deciding the stage sequence and compiling it into a `Pipeline` object are one decision in
two forms: `PLAN.md` is the form a person reads, the definition is the form SageMaker
executes. Splitting them into two skills lets the two drift, and a plan that disagrees
with the pipeline it produced is worse than either alone.

Four rules, each of which a trial run got right and is worth keeping right.

**Compile locally before creating anything.** Emit the definition, inspect it, and call no
create, upsert or start API until it has been read. A definition is a document; a pipeline
is a resource with a cost and a lifecycle.

**Every gate becomes a `ConditionStep` that fails closed.** The gate in `PLAN.md` and the
condition in the definition are the same rule; if the definition can reach registration
when the gate refuses, the definition is wrong whatever the plan says. An empty pass
branch and a `FailStep` failure branch is a correct pipeline, not an incomplete one.

**A definition whose code bundle is not yet immutable is not executable.** Mark it so. A
run recorded `readyForExecution: false` with the reason attached — the artefact exists, its
status is stated, and nobody mistakes a compiled document for a runnable one.

Each stage's own skill says how to write its step; this says how steps connect — ordering,
parameterisation, caching, resuming, and turning off auto-registration that would create
resources as a side effect. Step guidance written here belongs in the stage's skill.

