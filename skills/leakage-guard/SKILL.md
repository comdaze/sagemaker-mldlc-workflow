---
name: leakage-guard
description: Decides which inputs a supervised model may use, by asking whether each value was knowable at prediction time - covering point-in-time correctness, split hygiene, and a behavioural screen that refuses to train on an input that nearly is the target. Use before training any model, when choosing or reviewing an input set, when an offline score looks too good, or when deciding train/validation/test splits. Applies to tabular, time-series and deep-learning work in every partition.
---

# Leakage guard

One question decides admissibility:

> **Was this value knowable at prediction time?**

Everything below is machinery for answering it honestly, because the two ways of
getting it wrong both look like success. Refuse too much and you ship a model with
too little validated signal to support the decision it was built for. Refuse too
little and you ship an offline score that production will not reproduce — and that
failure is the expensive one, because every structural check passes on the way out.

**This body decides; the references measure.** What counts as a refusal is here and
holds in every modality. Which statistic implements it is in `references/`, and step 3
says which one to read.

**This skill is stage 4 of the sixteen, and its own steps are numbered separately.** The six
`Step N` headings below are internal to the screen — step 6 is not stage 6. The distinction is
worth the sentence because both numbering systems live in this power, and a reader arriving from
`next.py`'s `stage: 4` directive has no other way to tell them apart.

## Step 1: name the prediction time

"Prediction time" is domain-defined, and it is a decision, not a discovery. Write it
in the contract (`spec.data.predictionTime`) before selecting a single feature.

| Domain | Prediction time is | So this is inadmissible |
|---|---|---|
| Time-series / market forecast | the forecast cut-off — a wall-clock instant relative to each target period | any value published after the cut-off for that period |
| Credit / underwriting | the moment the application is submitted | anything from the servicing history that follows |
| Churn / retention | the end of the observation window | anything from the label window itself |
| Fraud / abuse | the moment the transaction is scored | the adjudication outcome, and anything derived from it |
| Predictive maintenance | the moment the alert would fire | readings after the failure, and the repair record |

Two consequences worth stating in the plan rather than discovering later. A single
model may have **one prediction time per prediction**, so a feature admissible for
a 24-hour-ahead forecast may be inadmissible for a 1-hour-ahead one — if both are
wanted, they are two contracts. And prediction time is not the same as *training*
time: the training set is assembled long after the fact, which is exactly why the
data has values that were not available then.

## Step 2: sort every candidate into three tiers

Only the third is a refusal. Sorting is a hypothesis to be tested in step 3, not a
conclusion.

| Tier | What it is | Verdict |
|---|---|---|
| **Structurally impossible to be late** | derived from the request itself or from the calendar — the submitted application's own fields, the image being classified, hour and weekday | Allowed, no assumption needed |
| **Available by design, unverified here** | a value an upstream party publishes before the decision point; a third-party score refreshed on a known schedule; a contract term agreed in advance; a representation whose fitting provenance is stated but not proven | **Allowed**, with the assumption recorded in the plan |
| **Known to be after the fact** | actuals, realized values, settlement and adjudication outcomes, final statuses, resolution codes, post-hoc labels, annotations informed by the outcome, and any aggregate computed from them | **Forbidden**, and the validator must reject it |

**The middle tier is where the damage happens, in both directions.**

Forbidding it is the responsible-looking failure. A plan that withholds every input
whose availability it cannot prove keeps only what is trivially safe, and what is
trivially safe is rarely enough to support the decision — hour-of-day cannot price a
market or score a loan. That is not a safe answer, it is a useless one, and it is
discovered late because the pipeline runs perfectly.

So the default for tier 2 is **allow and record**. A value named for something that
exists before the event is published before the event — that is what the thing *is*.
Reserve refusal for what you have positive reason to believe is late.

Anything in tier 3 that is genuinely needed becomes a **new input at a later
prediction time**, not an exception to the rule.

## Step 3: screen every candidate against the target — this is a refusal

Sorting by name is a hypothesis, and **names lie**. Run a screen that costs one pass
over the data, and treat it as a gate rather than a diagnostic.

The measured case this exists for: an input whose name described a value contracted
in advance was allowed into tier 2, and was in fact computed from the realized
outcome. Used **directly as the prediction, with no model at all**, it scored MAE
14.18 against a target whose standard deviation was 232, correlating 0.975 — better
than the trained model that had been permitted to use it. Every structural check
passed. The quality gate passed. The result was worthless.

```
one measured run, for scale — not the general baseline set
  naive: predict the mean            MAE 182.71
  naive: previous period, same slot  MAE 137.06
  the suspect input, used as-is      MAE  14.18   corr 0.975   ← the leak
  the trained model                  MAE  18.76   ← worse than the leak alone
```

### What refuses

Three refusals, stated in the form they take in every modality. The *statistic* that
implements each one differs; the *consequence* does not.

1. **An input that predicts the target too well on its own.** Whatever measure suits
   the modality, there is a bound, the bound is declared in the contract, and
   crossing it is a hard stop rather than a warning.
2. **An input whose direct use as the prediction lands within the declared margin of the
   quality gate.** If one input already nearly clears the bar the model must clear, the
   model is not the thing being measured.
3. **An input whose availability at prediction time cannot be established, and which
   there is positive reason to believe is late.** Tier 3 of step 2, mechanically.

An input that nearly *is* the target is either the target under another name or
computed from it. There is no third explanation worth training on, and no naming
convention will surface it — only the comparison will.

**Tier 2 is exemptable, and it is the only one.** The bound is derived from the strongest
baseline, and a baseline is usually a column of the data — so *any* good column scores near
it, and refusing every such column refuses good features along with leaks. The statistic
cannot separate the two; only a claim about **when the value is knowable** can. Name the
input in the contract's `assumedKnownAtPredictionTime` **with a reason** and its
direct-prediction refusal becomes a recorded assumption carrying the measurement, so the
claim and the size of the mistake-if-wrong sit together.

Tiers 1 and 3 are not exemptable. A correlation of 0.999 is not rescued by a claim about
timing — at that point the claim is the thing in doubt. And the self-check is passed an
empty exemption map, so no contract can disarm the probe by naming it.

**Beyond that, none of the three may be softened into a review step.** A constraint written
as a refusal survives into the artefacts; written as a suggestion it holds only while
someone remembers. Argue about a bound in the contract, where the argument is visible;
the consequence of crossing it is not negotiable at runtime.

### Read the reference for your modality — this is a step, not a footnote

The body above says what a refusal is. It does not say what to measure, because that
depends on what an input *is*. Do not write the screen without reading one of these:

| Your inputs | Read |
|---|---|
| Table columns, computed aggregates, third-party scores | `references/screening-scalar.md` |
| Images, text, audio, embeddings, any derived representation | `references/screening-unstructured.md` |
| **Additionally**, anything carrying a time axis | `references/temporal-sources.md` |

Record in `PLAN.md` which one you read. A screen written without it is a screen whose
bounds were invented, and the omission is otherwise invisible — the code runs and
reports PASS either way.

### Run the screen, and let it prove itself

```bash
SCREEN=$(ls ~/.kiro/powers/installed/*/skills/leakage-guard/scripts/leakage-screen.py \
         ./skills/leakage-guard/scripts/leakage-screen.py 2>/dev/null | head -1)
python3 "$SCREEN" data.csv contracts/feature-policy.json \
        --out artifacts/leakage-audit.json
```

Standard library only, so it runs where the data is — inside a processing job, inside a
built-in algorithm image, on a laptop. Requiring numpy to compute a correlation would put
the screen out of reach in exactly the places it belongs.

**It refuses to report at all if it cannot refuse.** Before screening anything, it builds
a probe from the target — the target plus 1% noise for a numeric target, the label with 1%
of values flipped for a binary one — pushes it through the same code path, and exits 2 if
the screen does not fire on it. A screen that has never refused anything is
indistinguishable from a screen with a sign error, and both report PASS. This is that
distinction, made on your data, in this run, before any verdict is printed.

So there are three outcomes, not two:

| Exit | Meaning |
|---|---|
| 0 | screened, probe fired, nothing refused |
| 1 | a candidate was refused — it is named, with the measurement and the bound it crossed |
| 2 | the screen could not be trusted: no bounds declared, no probe constructible, or the probe did not fire |

A candidate the statistics cannot reach — a text field against a numeric target, an image
path — is reported as **UNSCREENED**, not as passed. Screen those by the modality
reference and record the result; an unscreened candidate is not a cleared one.

### Prove the refusal fires, on data you control

The script above does this for you on every run, and it is worth knowing why it is built in
rather than left as a step someone remembers.

A real run did it unprompted, and it is the strongest thing in that run's evidence: the
audit recorded eight checks as PASS, and separately one probe that triggered both
statistical refusals. **The first half says the admissible inputs passed. Only the second
half says the checks work.**

Record the probe's own numbers in the audit artefact — which statistic, what value, which
bound it crossed — not merely that it was refused. `README.md` classifies this power's
constraints by whether "a test can prove the refusal fires"; this is that test, and until a
run carries it the classification is a claim about the code rather than a measurement of it.

If you screen by hand rather than with the script — a modality it does not cover — the
obligation is unchanged. Build the probe from data you already have; the target or the
label, lightly corrupted, is the cheapest inadmissible input that exists, and the
construction per modality is in the reference you read above.

**Never run a probe against the real input set and keep going.** Its purpose is to verify
the screen, so it lives in memory, never reaches a training channel, and never appears in
the input policy.

### Report the ratio, not just the score

Publish the model's error **beside the best single-input-as-prediction score and the
naive baselines**, on the release page and in the training report. A model that beats
its own quality gate by an order of magnitude has usually found a leak rather than a
signal, and that ratio makes it visible immediately instead of after deployment.
`baseline-first` is the stage that makes this unavoidable.

## Step 4: split hygiene — the other family

Point-in-time correctness governs *which inputs*. This governs *which samples*, and
it leaks just as thoroughly with a perfectly admissible input set.

| Failure | What it looks like | The rule |
|---|---|---|
| **Fit before split** | any fitted artefact — scaler, imputer, encoder, selector, tokeniser, embedding — produced over all samples, then applied per fold | fit on the training partition only, inside the fold |
| **Target-informed encoding** | a value replaced by a statistic of the target computed over every sample | compute out-of-fold, or from the training partition alone |
| **Group bleed** | the same customer, device, site, patient, recording or document in two partitions | split by group, never by sample |
| **Temporal bleed** | a random shuffle on time-ordered data | split chronologically; validation strictly later than training |
| **Duplicates** | the same sample in two partitions, exactly or near-exactly | de-duplicate before splitting, on a declared key or similarity bound |
| **Repeated tuning on the test set** | the held-out set consulted once per experiment | tune on validation; touch test once, at the end |

Two have a mechanical check worth writing, so they belong in the pipeline rather than
in a reviewer's memory: **assert the intersection of group keys across partitions is
empty**, and, where there is a time axis, **assert the validation period begins after
the training period ends**. Both are one line and both fail loudly.

The preprocessing rule has a structural form too: put every fitted transform inside
the pipeline step that also does the split, so no code path exists in which a
transform can see samples it should not. A transform fitted above the split — in a
notebook cell, or in a shared preparation script — is the classic shape of this bug.

Two rows need modality detail this body does not carry: deriving and declaring the
group when no such field exists, and the similarity bound that replaces an equality
key for near-duplicates, both in `references/screening-unstructured.md`. The temporal
assertions, including the lag-feature one that is usually skipped, are in
`references/temporal-sources.md`.

## Step 5: what goes in the contract

```yaml
spec:
  data:
    predictionTime: <the instant, and what it is relative to>
    leakageScreen:
      modality: scalar | unstructured | both
      reference: <which reference file the bounds came from>
      bounds: <the declared bounds -- names and values are modality-specific;
               see the reference for this modality>
    inputs:
      allowed: [<explicit list -- never "everything else">]
      forbidden: [<tier 3, by name and by pattern>]
      assumed:                          # tier 2
        - input: <name>
          reason: <why it is believed available at prediction time>
    split:
      strategy: chronological | grouped | stratified
      groupBy: <the group identity, when grouped -- a field, or how it is derived>
      boundaries: <dates or fractions>
```

Two properties matter more than the field names. Every bound is **declared here and
nowhere else**, so raising one is a reviewable act. And `reference` records which
modality's bounds were used, which is what distinguishes a screen that was configured
from one whose numbers were invented.

`inputs.allowed` is an explicit allowlist rather than an exclusion rule: when
something new appears upstream, an allowlist keeps the model unchanged until someone
decides, while "everything except the forbidden ones" silently absorbs it — including
when the new arrival is an actual. `forbidden` carries patterns as well as names
(`*_actual`, `*_final`, `resolution_*`, and whatever the domain's own post-hoc naming
is), because the next thing to leak has not been named yet.

## Step 6: record what you could not verify

Tier 2 rests on an assumption. Say so, in the plan, once per assumption — what is
assumed, what enforces it structurally anyway, and what specifically remains
unverified.

Do **not** demand proof of availability as a precondition for registering the
dataset. The record that would prove it — a per-source, per-period publication log,
or a documented annotation procedure — exists only where someone already built it.
Requiring it blocks the whole plan on an artefact nobody has. Enforce the boundary
structurally so a late value cannot enter an input, record the residual assumption,
and use the proof to *check* it later if it turns up. Its absence is a recorded gap,
not a stop.

`references/temporal-sources.md` has the wording for the time-axis case, which is
where this comes up most.

## The trap that names cannot warn you about

**Any input computed from the outcome is inadmissible, whatever it is called.** Worth
stating separately because the usual review heuristic — read the names, flag the
suspicious ones — is exactly what fails here: a value produced by a process that
concludes after the event is often *stamped* with a time before it, because it
describes that earlier period. So check how a value is produced, not what it is
called. The two recurring shapes and their remedies are in
`references/temporal-sources.md`.

When you cannot yet tell which tier an input belongs to, **name the check that would
decide it** and put that check in the plan as a task. A named, answerable check beats
both a deadlock and a silent exclusion — and the silent exclusion is worse, because it
leaves no trace that a decision was made.

## What this skill does not cover

Adversarial or intentional label leakage, and privacy-motivated feature exclusion.
Both are real concerns with different machinery; nothing here addresses them, and
saying so is better than implying coverage.
