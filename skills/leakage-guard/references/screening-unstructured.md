# Screening unstructured inputs

The checks for part 3 when the input is an image, a document, an audio clip or a
derived representation. `SKILL.md` says what a refusal is and that it does not
proceed; this says what to measure.

The scalar checks do not transfer. There is no correlation between a JPEG and a
label, and a per-pixel statistic answers nothing. But the question is unchanged —
*was this knowable at prediction time* — and leakage here is just as fatal and
rather easier to ship, because nothing in the pipeline looks wrong.

For table columns and computed scores, read `screening-scalar.md`. When the source
has a time axis — frames from a recording, documents with a publication date — read
`temporal-sources.md` as well.

## What refuses

Four checks. Each is a hard stop, and each has a bound or a rule declared in the
contract rather than chosen at runtime.

### 1. The label is recoverable from something other than the content

The classic and most common leak in unstructured data. Before measuring anything
about the content, ask what a model could learn from everything *around* it:

- **Filename or path.** `train/pneumonia/0031.jpg` hands over the label. So does an
  id whose numeric range differs per class, and so does sort order.
- **File metadata.** EXIF device, capture software, resolution, colour profile,
  compression settings, PDF producer string, document author.
- **Provenance shortcuts.** One class collected at one site or one time and another
  class elsewhere. The model learns the scanner, not the disease; the sensor, not
  the defect.

The refusal: train a **trivial model on the metadata alone** — path string, EXIF
fields, file size, dimensions — and refuse if it beats the naive baseline by more
than the declared margin. This is the direct-as-prediction check from the scalar
reference, applied to the wrapper instead of the content.

### 2. The same sample appears in more than one partition

Exact duplicates are the easy case. The ones that matter are near-duplicates:

- the same photograph resized, recompressed, cropped or watermarked
- the same document under two ids, or with boilerplate differing
- consecutive frames from one recording, or overlapping audio windows
- augmented copies generated **before** the split rather than after

The refusal: a perceptual or embedding-space near-duplicate scan **across
partitions**, refusing above a declared similarity bound. Frames, pages and
segments must be grouped by their source recording, document or subject and split
by that group — never by item. This is `SKILL.md`'s group-bleed rule, and here the
group is almost never a column that already exists; it has to be derived and
declared.

### 3. A representation was produced with knowledge it should not have

Embeddings, tokenisers and feature extractors are themselves fitted artefacts:

- an embedding model fine-tuned on the full dataset, then used to encode all splits
- a tokeniser or vocabulary built over train and test together
- a dimensionality reduction or clustering fitted before the split
- a pretrained checkpoint whose own training data includes the evaluation set — the
  hardest to detect and worth stating as an assumption when it cannot be ruled out

The refusal: every fitted representation is produced inside the split, from the
training partition only, and the artefact carries a digest recorded in the contract.
An embedding whose provenance cannot be established is tier 2 in `SKILL.md`'s
scheme: allowed with the assumption recorded, not silently trusted.

### 4. Annotation was informed by the outcome

Labels for unstructured data are usually produced by people, sometimes after the
fact and sometimes while looking at material the model will not have:

- a radiologist labelling with the eventual diagnosis in front of them
- a moderator labelling with the enforcement decision already made
- text labelled from a downstream field that postdates the prediction time

This is not detectable from the files. It is established by asking how the
annotation was produced, and recorded under `features.assumed[].reason` when the
answer cannot be verified.

## The probe

Same obligation as the scalar case: prove the screen fires.

```python
# Leak the label into the wrapper, not the content.
for path, label in samples:
    probe_path = f"{tmp}/{label}/{basename(path)}"      # label in the path
assert metadata_screen(probe_paths) is REFUSED, "the metadata screen did not fire"

# Leak by duplication across partitions.
train_probe = train + [augment(x) for x in val[:k]]
assert duplicate_screen(train_probe, val) is REFUSED, "the duplicate scan did not fire"
```

One probe per check that has one. Record which check fired, on what evidence, and at
what value — a screen that has never refused anything is indistinguishable from one
with a sign error, and both report PASS.

## What to record in the contract

```yaml
spec:
  data:
    leakageScreen:
      metadataModelMarginPct: 20       # trivial-model-on-metadata bound
      maxCrossPartitionSimilarity: 0.9 # near-duplicate bound, embedding space
      groupBy: <recording | document | subject | site>
      representations:
        - artefact: <embedding or tokeniser>
          digest: <sha256>
          fittedOn: training-partition-only | unverified
```
