# Splitting by modality

Contents:
- What a prediction unit and a complete unit are, per modality
- What a group is, and why it is rarely a field that already exists
- Split mechanics: tables, time series, image and text corpora
- Sample counts and what has to sum to what

`SKILL.md` says assert completeness, execute the split so nothing sees across it, and
refuse rather than warn. This says what those mean for your data.

## Prediction units and completeness

A **prediction unit** is the thing one prediction is about. Completeness is a property of
that unit, and it is the assertion `SKILL.md` requires before anything is trusted.

| Modality | Prediction unit | Complete means |
|---|---|---|
| Tabular classification | one record | every required field present; the label present exactly once |
| Tabular regression | one record | the same, plus the target within its declared range |
| Time series | one period at one horizon | every interval in the period present exactly once, and no interval from a neighbouring period |
| Image / text corpus | one item, or one group of items | the item readable and decodable; the group's item count matching its declared size |

The time-series row is the one that bites. Two failures recur:

**A period that is missing intervals** trains fine and scores fine. Assert the count per
period before computing a single feature.

**A boundary written two ways.** Midnight rendered as `24:00` of one day parses in most
datetime libraries as `00:00` of the next, so the last interval of a period silently
becomes the first of the following one, shifting labels by one position at exactly the
boundary a split is drawn on. A run found this only because it asserted interval counts
per period first. `leakage-guard/references/temporal-sources.md` has the detail.

## What a group is

`leakage-guard` may choose a grouped split. The group is the thing that must not appear
in two partitions, and **it is rarely a field that already exists**:

| Modality | The group is usually |
|---|---|
| Tabular | customer, account, device, patient — often present, sometimes needs deriving from a join |
| Time series | the period, or the entity a series belongs to — a meter, a store, a sensor |
| Images | the subject, the capture session, the site or the scanner |
| Text | the document, the author, the thread, or the source publication |
| Any of these, augmented | the **original** item, never its augmented copies |

That last row is the most common unstructured mistake: augmentation applied before the
split puts near-copies of one item on both sides. Augment after splitting, or group by
the original.

Whatever it is, declare it as `spec.data.split.groupBy` and record how it was derived if
it was derived. A group key nobody can reconstruct is not auditable.

## Table splits

Chronological when there is a time axis, grouped when repeated entities exist, stratified
when the class balance is thin — and these compose. A stratified split within a
chronological one is normal; a stratified split *instead of* a chronological one on
time-ordered data is temporal bleed wearing the right vocabulary.

Assertions worth writing:

```python
assert set(train[group]) & set(val[group]) == set()
assert len(train) + len(val) + len(test) == len(filtered_input)
```

The second is the one people leave out, and it is what catches rows silently dropped by
a filter nobody remembers writing.

## Time-series splits

Contiguous blocks in time order, never a shuffle. Validation strictly later than
training, test strictly later than validation, and each boundary drawn **between**
prediction units rather than inside one.

Two further rules:

- **A unit is never split across partitions.** Half a period in training and half in
  validation is leakage even with an admissible input set.
- **Lag and rolling features respect each unit's own cut-off**, not the latest one. A
  global cut-off is correct for the final unit and leaks for every earlier one, which
  aggregates away and is obvious per unit.

## Image and text corpus splits

There are no columns here, so the split is over items and their groups.

- Split on the group, then verify no near-duplicates cross the boundary. Exact-hash
  de-duplication is not enough: resized, recompressed, cropped and watermarked copies all
  hash differently. `leakage-guard/references/screening-unstructured.md` has the
  similarity check and the bound.
- **Keep the manifest, not the directory layout, as the source of truth.** A
  `train/`-and-`val/` directory structure encodes the split into the filesystem, which
  means re-splitting means moving files, and a file that was moved once has no record of
  where it was. A manifest naming item, group and partition re-splits by rewriting one
  file.
- Class balance per partition is worth asserting, not assuming, because a grouped split
  over an imbalanced corpus routinely produces a partition missing a class entirely.

## What has to sum to what

Recorded in the processing report, and checked by `contract-check.py`:

```
input sampleCount
  = filtered-out count + sum of partition counts
```

Both terms matter. Without the filtered-out count, a filter that dropped a third of the
data looks like a smaller dataset rather than a decision nobody recorded. A trial run
recorded exactly this — five rows dropped for missing targets, stated in the report —
which is why its counts could be reconciled at all.
