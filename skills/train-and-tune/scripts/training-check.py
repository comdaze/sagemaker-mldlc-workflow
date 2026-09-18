#!/usr/bin/env python3
"""Check baseline, training and tuning records against the train-and-tune contract.

The roadmap named this script `baselines.py`. It is not, and the reason is worth stating:
computing a baseline is five lines and the five lines differ per task -- a mean, a
majority class, a persistence rule, a nearest neighbour over embeddings -- so a script
that computed them would work for one data shape and mislead for the others. Those
snippets belong in references/baselines-and-search.md, where they can be modality-
specific without pretending to be universal.

What is universal is the RECORD: that baselines exist, that the bound was derived from the
strongest one, that the image and inputs are pinned by digest, that no tuning run saw the
test set, and that the winner was fixed before test access. Those are checkable for every
task, and a check that exits non-zero is the only form in which a rule holds.

Offline by design: it reads what the run recorded rather than calling AWS.

Usage:
    training-check.py <baseline-report.json> [<training-report.json>] [<tuning-report.json>]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

NULLISH = {None, "", "null", "None", "nil", "undefined"}
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.I)

# Metrics where a SMALLER value is better. Anything not listed is treated as
# larger-is-better, and the script says so rather than guessing silently.
LOWER_IS_BETTER = {
    "mae", "mse", "rmse", "mape", "smape", "logloss", "log_loss", "brier",
    "crps", "pinball", "wape", "medae", "msle",
}


class Report:
    def __init__(self) -> None:
        self.violations: list[str] = []
        self.notes: list[str] = []

    def fail(self, check: str, message: str) -> None:
        self.violations.append(f"[{check}] {message}")

    def note(self, message: str) -> None:
        self.notes.append(message)

    @property
    def ok(self) -> bool:
        return not self.violations


def load(path: Path, r: Report) -> dict | None:
    if not path.is_file():
        r.fail("input", f"no such file: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        r.fail("input", f"{path} is not valid JSON: {e}")
        return None


def better(a: float, b: float, metric: str) -> bool:
    """Is a better than b, for this metric?"""
    return a < b if metric.lower() in LOWER_IS_BETTER else a > b


def check_baselines(b: dict, r: Report) -> tuple[str, float] | None:
    """Stage 6: baselines exist, and the bound comes from the strongest one."""
    computed = b.get("computed")
    if not computed:
        r.fail(
            "baselines",
            "no baselines recorded. A model's score is uninterpretable without them, "
            "and a run that trains first treats whatever number it gets as the result.",
        )
        return None

    metric = b.get("metric")
    if metric in NULLISH:
        r.fail("baselines", "no metric recorded; a score without its metric is a number.")
        return None
    if metric.lower() not in LOWER_IS_BETTER:
        r.note(
            f"metric {metric!r} is not in the lower-is-better list, so it is treated as "
            "larger-is-better. Check that is right for your metric."
        )

    scores: dict[str, float] = {}
    for entry in computed:
        if not isinstance(entry, dict) or "name" not in entry or "score" not in entry:
            r.fail("baselines", f"malformed baseline entry: {entry!r}")
            continue
        if not isinstance(entry["score"], (int, float)):
            r.fail("baselines", f"baseline {entry['name']!r} score is not a number.")
            continue
        scores[entry["name"]] = float(entry["score"])

    if len(scores) < 2:
        r.fail(
            "baselines",
            f"only {len(scores)} baseline(s) recorded. Every task has at least two, and "
            "one alone gives no sense of the spread a model has to beat.",
        )

    if not scores:
        return None

    claimed = b.get("strongest")
    actual = min(scores, key=scores.get) if metric.lower() in LOWER_IS_BETTER \
        else max(scores, key=scores.get)

    if claimed in NULLISH:
        r.fail("baselines", "no strongest baseline named; the bound has to derive from one.")
    elif claimed not in scores:
        r.fail("baselines", f"strongest is {claimed!r}, which is not among the computed set.")
    elif claimed != actual:
        r.fail(
            "baselines",
            f"strongest is recorded as {claimed!r} ({scores[claimed]}) but {actual!r} "
            f"({scores[actual]}) is better on {metric}. Deriving the bound from the "
            "weaker comparison sets a bar the model clears without being useful.",
        )

    return (actual, scores[actual])


def check_bound(b: dict, strongest: tuple[str, float] | None, r: Report) -> None:
    """The declared bound improves on the strongest baseline by the declared margin."""
    q = b.get("quality")
    if not isinstance(q, dict):
        r.fail(
            "bound",
            "no quality bound recorded in the baseline report. The bound is derived at "
            "this stage, while the model's score does not yet exist -- deriving it later "
            "is indistinguishable from fitting it to the result.",
        )
        return

    for field in ("metric", "bound", "marginPct", "contract"):
        if field not in q:
            r.fail("bound", f"quality is missing {field!r}.")

    if strongest is None or "bound" not in q:
        return

    name, base = strongest
    bound = q["bound"]
    metric = q.get("metric") or b.get("metric") or ""

    if not isinstance(bound, (int, float)):
        r.fail("bound", f"bound {bound!r} is not a number.")
        return

    if not better(bound, base, metric):
        r.fail(
            "bound",
            f"bound {bound} is not better than the strongest baseline {name!r} ({base}) "
            f"on {metric}. A gate a baseline already passes gates nothing.",
        )
        return

    margin = q.get("marginPct")
    if isinstance(margin, (int, float)) and base:
        expected = base * (1 - margin / 100) if metric.lower() in LOWER_IS_BETTER \
            else base * (1 + margin / 100)
        if abs(bound - expected) > abs(expected) * 0.02:
            r.fail(
                "bound",
                f"bound {bound} does not match {name!r} ({base}) improved by "
                f"{margin}% ({expected:.6g}). Either the margin or the bound was "
                "changed without the other.",
            )


def check_training(t: dict, r: Report) -> None:
    """Stage 7: the run is pinned, and it produced something."""
    for field, why in (
        ("imageDigest", "a tag moves, and then the run cannot be rebuilt"),
        ("inputDigests", "a training job takes a path, and a path is not an identity"),
        ("resolvedHyperparameters",
         "a default that changed between SDK versions is invisible in intent"),
    ):
        if field not in t:
            r.fail("training", f"training report is missing {field!r} -- {why}.")

    d = t.get("imageDigest")
    if d not in NULLISH and not DIGEST_RE.match(str(d)):
        r.fail(
            "training",
            f"imageDigest {d!r} is not a sha256 digest. A tag is not a pin: ':latest' "
            "makes rollback a fiction.",
        )

    for ch, dig in (t.get("inputDigests") or {}).items():
        if ch.lower() == "test":
            r.fail(
                "training",
                f"training report records a {ch!r} channel. A test channel does not "
                "exist in a training job; if the code has one, that is a defect whether "
                "or not it is currently pointed anywhere.",
            )
        if dig in NULLISH or not DIGEST_RE.match(str(dig)):
            r.fail("training", f"input channel {ch!r} digest {dig!r} is not a sha256 digest.")

    art = t.get("modelArtefact")
    if not art or (isinstance(art, dict) and art.get("uri") in NULLISH):
        r.fail(
            "training",
            "training report records no model artefact. Success is exit code AND "
            "artefact: a job that finishes cleanly and writes no model is a failure "
            "reporting as a success.",
        )
    elif isinstance(art, dict) and art.get("versionId") in NULLISH:
        r.fail(
            "training",
            "model artefact has no object version. A path can be overwritten, so the "
            "release cannot later prove which bytes it approved.",
        )

    if t.get("billableSeconds") is None:
        r.note(
            "no billableSeconds recorded -- not a violation, but 'train a bigger one' "
            "should be a decision with a number attached."
        )


def check_tuning(t: dict, r: Report) -> None:
    """Stage 8: validation only, candidates declared, winner fixed before test."""
    method = t.get("method")
    METHODS = {"fixed-candidates", "amt-search"}
    if method not in METHODS:
        r.fail(
            "tuning",
            f"method is {method!r}; declare one of {sorted(METHODS)}. Tuning has two shapes "
            "and they differ by orders of magnitude in cost: a fixed candidate list launches "
            "exactly as many jobs as it names, while an AMT search explores ranges until its "
            "budget is spent. Which one runs is a spending decision, so it has to be on the "
            "record as a decision rather than inferable from the shape of the report.",
        )
    if t.get("methodChosenBy") != "user":
        r.fail(
            "tuning",
            "methodChosenBy is not 'user'. Put both options to them with the cost of each "
            "named -- how many jobs a fixed list launches, and what budget bounds an AMT "
            "search -- and record their answer. A run picked fixed parallel candidates "
            "without asking, and nothing objected because this check did not exist; worse, "
            "the check that did exist demanded a `candidates` list, so the tool itself was "
            "pushing toward one of the two answers.",
        )

    if method == "amt-search":
        # An AMT search declares ranges, not a list. Requiring `candidates` of it -- which
        # this checker did -- made the search method fail structurally, which is how a checker
        # ends up choosing a method on the user's behalf.
        space = t.get("searchSpace")
        if not isinstance(space, dict) or not space:
            r.fail(
                "tuning",
                "method is amt-search and no searchSpace is declared. The ranges are what "
                "stands in for a candidate list: declared before running, so the space "
                "cannot grow once a favourite appears.",
            )
        if t.get("maxJobs") in NULLISH:
            r.fail(
                "tuning",
                "method is amt-search and no maxJobs is recorded. An unbounded search has "
                "no declared stopping point, and cost is the reason the user was asked.",
            )
        ran = t.get("candidatesRun")
        mx = t.get("maxJobs")
        if isinstance(ran, int) and isinstance(mx, int) and ran > mx:
            r.fail(
                "tuning",
                f"{ran} jobs ran against a declared maxJobs of {mx}. The budget was exceeded "
                "or it was raised after the search began.",
            )
        declared = None
    else:
        declared = t.get("candidates")

    if method == "fixed-candidates" and not declared:
        r.fail(
            "tuning",
            "no declared candidate list. Picking the best of six on validation earns a "
            "smaller claim than picking the best of one, and a reader who does not know "
            "the count cannot make that adjustment.",
        )

    ran = t.get("candidatesRun")
    if isinstance(declared, list) and isinstance(ran, int) and ran != len(declared):
        r.fail(
            "tuning",
            f"{ran} candidates ran but {len(declared)} were declared. The search either "
            "exceeded its declared space or stopped early without recording why.",
        )

    if t.get("budget") in NULLISH:
        r.fail(
            "tuning",
            "no budget recorded. A declared budget ends the search at a point chosen "
            "before anyone had a favourite.",
        )

    sel = t.get("selectOn")
    if sel is None:
        r.fail("tuning", "tuning report does not record what the winner was selected on.")
    elif str(sel).lower() != "validation":
        r.fail(
            "tuning",
            f"winner selected on {sel!r}. Tuning may read validation; test is read once, "
            "after the winner is fixed.",
        )

    for c in t.get("channels") or []:
        if str(c).lower() == "test":
            r.fail(
                "tuning",
                "a tuning run carries a test channel. This is the refusal whose absence "
                "cannot be noticed from the outputs -- a comparison that can see the "
                "held-out set consumes it one experiment at a time, and every individual "
                "run still looks honest.",
            )

    if t.get("winnerFixedBeforeTestAccess") is not True:
        r.fail(
            "tuning",
            "winnerFixedBeforeTestAccess is not recorded as true. The ordering IS the "
            "claim: choosing among candidates on the test score and then reporting that "
            "score as the evaluation is indistinguishable in the artefacts from an "
            "honest single evaluation unless the order was written down.",
        )

    w = t.get("winner")
    if not w:
        r.fail("tuning", "no winner recorded.")
    elif isinstance(w, dict) and w.get("testScore") is not None:
        r.fail(
            "tuning",
            "the tuning report carries a test score for the winner. The test set is "
            "scored once, by evaluate-and-gate, after this stage hands the winner over.",
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("baselines", type=Path, help="artifacts/baseline-report.json")
    ap.add_argument("training", type=Path, nargs="?", help="artifacts/training-report.json")
    ap.add_argument("tuning", type=Path, nargs="?", help="artifacts/tuning-report.json")
    args = ap.parse_args()

    r = Report()
    checked = []

    b = load(args.baselines, r)
    if b is not None:
        strongest = check_baselines(b, r)
        check_bound(b, strongest, r)
        checked.append("baselines")

    if args.training is not None:
        t = load(args.training, r)
        if t is not None:
            check_training(t, r)
            checked.append("training")

    if args.tuning is not None:
        t = load(args.tuning, r)
        if t is not None:
            check_tuning(t, r)
            checked.append("tuning")

    for n in r.notes:
        print(f"NOTE  {n}")

    if r.ok:
        print(f"OK  {', '.join(checked)} satisfy the train-and-tune contract.")
        return 0

    print(f"\n{len(r.violations)} violation(s):", file=sys.stderr)
    for v in r.violations:
        print(f"  {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
