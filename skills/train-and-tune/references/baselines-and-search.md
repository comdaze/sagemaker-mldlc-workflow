# Baselines and search spaces

Contents:
- Which baselines are appropriate per task, with the code for each
- How to pick the strongest one, and the trap in doing so
- Search space shapes: trees, linear models, networks
- Budget: what to spend it on first

`SKILL.md` says compute baselines before any model, derive the bound from the strongest
one, and declare candidates before running them. This says which baselines and which
candidates.

## Baselines by task

Every one of these is a prediction rule with no learned parameters, computed on the
training partition and scored on validation and test like any other predictor.

### Regression

| Baseline | Rule | When it is the strongest |
|---|---|---|
| Mean | predict the training mean | almost never — it is the floor, not the bar |
| Median | predict the training median | skewed targets, and when the metric is MAE |
| Persistence | predict the last known value | any target with autocorrelation |
| Seasonal persistence | predict the value one season back at the same position | strong daily or weekly cycles |
| Domain heuristic | whatever the humans currently use | usually this one |

```python
mean_pred      = np.full(len(y_val), y_train.mean())
median_pred    = np.full(len(y_val), np.median(y_train))
persistence    = y_val.shift(1)                    # one step back
seasonal       = y_val.shift(season_length)         # one season back
```

### Classification

| Baseline | Rule | When it is the strongest |
|---|---|---|
| Majority class | always predict the most frequent class | imbalanced problems, and it is a harsh floor for accuracy |
| Class prior | predict the training class frequencies as probabilities | when the metric is log loss or Brier |
| Stratified random | sample from the training distribution | a sanity check on the metric itself |
| Single strongest input | threshold the one most predictive input | when a business rule already exists |

```python
majority   = np.full(len(y_val), y_train.mode()[0])
prior      = np.tile(y_train.value_counts(normalize=True).values, (len(y_val), 1))
```

The single-strongest-input baseline overlaps with `leakage-guard`'s screen on purpose. If
it wins, the question is not "what a good baseline" but "why is one input that
predictive".

### Unstructured deep learning

The naive rules still exist, and they are the ones people skip because there is no column
to average.

| Baseline | Rule |
|---|---|
| Majority class | as above; still the floor |
| Metadata-only model | a trivial model on file size, dimensions, EXIF, path depth — see `leakage-guard/references/screening-unstructured.md`, where the same computation is a leak check |
| Nearest neighbour on off-the-shelf embeddings | encode with a pretrained model, predict the nearest training item's label |
| Zero-shot or off-the-shelf classifier | whatever a general-purpose model gives with no training |

The last two matter more here than anywhere else, because they are frequently competitive
with a fine-tuned model on a small corpus, and discovering that after the training spend
is expensive.

## Picking the strongest, and the trap

The bound comes from the strongest baseline that is **valid at prediction time**. Two
failure modes:

**Choosing the weakest.** Deriving the bound from the mean when a persistence baseline is
three times better sets a bar the model clears without being useful. This is the common
one and it is usually not deliberate — the mean is the baseline everyone remembers to
compute.

**Choosing an invalid one.** A baseline built from a value that is not knowable at
prediction time is not a baseline, it is `leakage-guard`'s screen firing in a different
costume. If your strongest "baseline" is suspiciously good, screen it as an input before
using it as a bar.

## Search spaces

Declared before running, in the contract. Small and explicit beats large and clever: a
declared list of six candidates produces a recorded count and a bounded spend, while an
open search produces neither.

### Gradient-boosted trees

Order matters — these are roughly in decreasing order of effect:

| Parameter | Sensible span | Note |
|---|---|---|
| number of rounds | wide, with early stopping on validation | early stopping does most of the work |
| learning rate | 0.01 – 0.3, log scale | trades against rounds; do not tune both freely |
| max depth | 3 – 10 | the main capacity knob |
| min child weight / min samples per leaf | 1 – 20 | the main regularisation knob for noisy targets |
| subsample, colsample | 0.5 – 1.0 | cheap variance reduction |
| L1 / L2 | log scale | last, and only if overfitting persists |

### Linear and regularised linear

One parameter that matters (the regularisation strength, on a log scale), one that
sometimes does (the L1/L2 mix). If a linear model needs a large search, the feature
representation is the thing to change instead.

### Networks

| Parameter | Note |
|---|---|
| learning rate | the one that matters most, by a wide margin; log scale |
| batch size | interacts with learning rate; change them together or neither |
| epochs | prefer early stopping on validation over tuning this |
| weight decay | log scale |
| architecture depth or width | expensive; change last and one at a time |

Fine-tuning a pretrained checkpoint has a different shape: the learning rate wants to be
one or two orders smaller, and how many layers are unfrozen usually matters more than any
numeric parameter.

## Spending the budget

In order, because the first two often make the rest unnecessary:

1. **Fix the inputs.** A screened, correctly split input set beats any hyperparameter.
2. **Get early stopping working**, on validation, with the right metric.
3. **Then tune capacity** — depth, width, regularisation.
4. **Only then tune the small numbers.**

A search that starts at item 4 spends real money establishing that item 4 does not matter
much.
