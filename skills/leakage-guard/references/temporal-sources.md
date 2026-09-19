# Temporal sources

Read this whenever any input carries a time axis, whatever its modality: a table of
readings, documents with publication dates, frames from a recording. It is the
cross-cutting reference — the two screening references cover *what to measure about
an input*, this covers *when the value came into existence*.

Nothing here is optional for a forecasting problem. For a problem with no time axis
at all — a single image classified on demand, a table where every row is independent
— none of it applies, and saying so is why it lives here instead of in `SKILL.md`.

## The trap that names cannot warn you about

Two shapes recur, and in both the name reads as innocent.

**A settled or resolved value is not a prediction.** Anything produced by a process
that concludes *after* the event is computed from the outcome by definition, however
its column is labelled — settlement, clearing, reconciliation, adjudication, final
status, resolution code, confirmed quantity, adjusted total. The trap is that such a
value is often stamped with a date *before* the target period, because it describes
that period; it was still produced afterwards.

So check the process timetable, not the word. A value described as belonging to the
day before the target may be published after a morning cut-off on the target day.

**An observation is not a forecast.** Weather, sensor, telemetry and market
observations pulled from a historical archive are usually reanalysis or actuals —
what *happened*. Using them as inputs for the target period is leakage no metadata
can repair, because the value did not exist at the prediction time.

The remedy is a different data pull: what the forecast said at that past moment, not
what the record now says happened. A publication-time column on an archive of actuals
documents when the archive was written, which is not the same fact.

## Publication time versus observation time

Three timestamps usually exist and they are routinely conflated:

| Timestamp | What it means | Use |
|---|---|---|
| **Observation time** | the instant the value describes | the join key |
| **Publication time** | the instant the value became available | the admissibility test |
| **Ingestion time** | the instant your pipeline stored it | neither — it reflects your schedule |

Admissibility compares **publication time** with the prediction time. Joining on
observation time and assuming availability is the single most common way a
point-in-time error enters a pipeline that otherwise looks careful.

## Do not demand a publication-proof record

A per-period, per-source `published_at` table exists only where an upstream platform
already produces one. Requiring it as a precondition blocks the whole plan on an
artefact nobody has.

So the default is `SKILL.md`'s tier 2: **allow and record**. Enforce the cut-off
structurally, so a late value cannot enter an input even if the source was late, and
write the residual assumption down:

```markdown
## Constraints traded away

- Upstream publication punctuality is assumed, not verified: no per-period
  publication-time record exists for these sources. The pipeline enforces the
  cut-off structurally, so a late value cannot enter an input; what is unverified is
  whether the source was ever late across the history trained on.
```

If a publication-time record does exist, use it to *check* the assumption later. Its
absence is a recorded gap, not a stop.

## When you cannot tell which tier it is

Name the check that would decide it, and put that check in the plan as a task:

> Is this an archived observation, or the forecast that was issued before the
> cut-off? Which upstream endpoint produced it, and does that endpoint expose the
> value as of a past moment?

A named, answerable check beats both a deadlock and a silent exclusion. A silent
exclusion is the worse of the two, because it leaves no trace that a decision was
made at all.

## Assertions worth writing into the pipeline

```python
# Validation strictly later than training. One line, fails loudly.
assert train[ts].max() < validation[ts].min()

# Every lag or rolling statistic is computed from values published before the
# cut-off for ITS OWN target period -- not before the cut-off of the latest one.
assert lag_source_publication <= cutoff_for(target_period)
```

The second is the one that gets skipped. A lag feature built with a single global
cut-off is correct for the last target period and leaks for every earlier one, which
is invisible in aggregate and obvious per period.

## A boundary condition that has caused real damage

Midnight is written two ways, and one of them belongs to the previous period. A
timestamp rendered as `24:00` of one day parses, in most datetime libraries, as
`00:00` of the *next* day — so the final interval of a period silently becomes the
first interval of the following one, shifting every label by one position at exactly
the boundary the split is drawn on.

A run found this by asserting that each period had its expected number of intervals
before trusting any of them. That assertion is cheap and it is the only reason the
error surfaced before training rather than after.
