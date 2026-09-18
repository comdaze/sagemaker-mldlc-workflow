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

    # The two decisions that settle whether a model ever ships, and by what standard. Audited
    # for after the algorithm: `marginPct` was read in five places and `metric` in forty-eight,
    # and neither had to be anybody's decision.
    #
    # The margin IS the ship/do-not-ship line. Beating the strongest baseline by 1%, by 5% or by
    # 20% before a model is worth operating is a business judgement -- nothing in the data, the
    # algorithm or this script determines it, and a run that picks it has decided the outcome of
    # the gate in advance.
    #
    # The metric decides what "good" means, and different metrics select different models: MAE,
    # RMSE and a daily P95 do not rank the same candidate the same way. `metrics-by-task.md`
    # offers several per task precisely because none of them is the answer.
    #
    # Checked HERE, at stage 6, because that is where the bound is declared -- before any score
    # exists. Asking afterwards is asking once the answer is known.
    if q.get("marginChosenBy") != "user":
        r.fail(
            "bound",
            f"marginPct is {q.get('marginPct')!r} and marginChosenBy is not 'user'. This margin "
            "is the line between shipping and not shipping: it says how much better than the "
            "strongest baseline a model must be before it is worth operating. Nothing derives "
            "it. Put it to the user with the baseline's own score beside it, and record their "
            "answer -- while the model's score still does not exist.",
        )
    if q.get("metricChosenBy") != "user":
        r.fail(
            "bound",
            f"metric is {q.get('metric')!r} and metricChosenBy is not 'user'. The primary metric "
            "decides what 'good' means, and MAE, RMSE and a daily P95 do not rank the same "
            "candidates the same way -- so choosing it chooses the model. "
            "`references/metrics-by-task.md` lists several per task because none of them is the "
            "answer; name the ones offered and record which the user picked.",
        )

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

    check_algorithm(t, r)
    check_compute(t, r, "training")

    if t.get("billableSeconds") is None:
        r.note(
            "no billableSeconds recorded -- not a violation, but 'train a bigger one' "
            "should be a decision with a number attached."
        )


def check_algorithm(d: dict, r: Report) -> None:
    """The algorithm is the user's choice, and recording alternatives is what makes it one.

    Nothing here asked. `training-check.py` read thirty fields and not one was the algorithm,
    while `references/baselines-and-search.md` documents search spaces for trees, linear models
    AND networks -- so this power already stated that several families are legitimate, and then
    let whichever one a run reached for go unrecorded.

    It is the most consequential of the choices audited so far. The tuning method decides what a
    search costs; the algorithm decides what the model can express, what the serving stack is,
    and who maintains it afterwards. A run that picks it silently has settled the shape of
    everything downstream.

    `algorithmAlternatives` is required, and must name something OTHER than the choice, because
    of what the tuning defect taught: a checker whose required fields admitted only one legitimate
    shape made "the user chose it" hollow. A choice among one option is not a choice, and
    recording what was offered is the cheapest way to keep that honest.

    `runtimeMode` is accounting rather than a refusal -- built-in, BYOS, extended, BYOC and BYOM
    differ in maintenance cost rather than in whether they work, so it must be visible without
    needing a signature.
    """
    algo = d.get("algorithm")
    if algo in NULLISH:
        r.fail(
            "training",
            "no algorithm recorded. It decides what the model can express, what the serving "
            "stack is and who maintains it -- so the one thing it must not be is the first "
            "thing that came to hand. Record it by name, as it was actually used.",
        )
        return
    if d.get("algorithmChosenBy") != "user":
        r.fail(
            "training",
            f"algorithm is {algo!r} and algorithmChosenBy is not 'user'. Put the candidates to "
            "them with what each implies -- what it can express, what it costs to train, what "
            "has to be maintained to serve it -- and record their answer. "
            "`references/baselines-and-search.md` covers trees, linear models and networks, so "
            "this power has never claimed one of them is the answer.",
        )
    alts = d.get("algorithmAlternatives")
    if not isinstance(alts, list) or not alts:
        r.fail(
            "training",
            "no algorithmAlternatives recorded. A choice presented as the only option is not a "
            "choice, and this exact defect has already been found once in this power: a checker "
            "required a shape that only one method could produce, which made the user's "
            "agreement to it meaningless. Name what else was offered.",
        )
    elif not [x for x in alts if str(x).strip().lower() != str(algo).strip().lower()]:
        r.fail(
            "training",
            f"algorithmAlternatives lists only {algo!r} itself. That is a menu with one item; "
            "record at least one genuine alternative that was put to the user.",
        )

    mode = d.get("runtimeMode")
    KNOWN = {"built-in", "byos", "extended", "byoc", "byom"}
    if mode in NULLISH:
        r.note(
            "no runtimeMode recorded -- not a violation, but built-in, BYOS, extended, BYOC and "
            "BYOM differ in what someone has to maintain, and that is worth stating once."
        )
    elif str(mode).strip().lower() not in KNOWN:
        r.fail("training", f"runtimeMode is {mode!r}; expected one of {sorted(KNOWN)}.")


def check_compute(d: dict, r: Report, where: str) -> None:
    """The compute decision is a spending decision, and this power had no place for it.

    Audited for it rather than assuming: `ml.` appears in no skill body, Spot appears nowhere in
    the repository, and no checker read an instance field. So every job picked an instance type
    and a count, and nothing recorded which -- a number nobody wrote down cannot be reviewed,
    and "train a bigger one" cannot be a decision with a number attached if the last number was
    never kept.

    Spot is the sharper half. Silence about it is not neutrality: absent a declaration the job
    runs on-demand, so the expensive option was chosen by omission. AWS documents Spot as the
    cost lever for exactly this workload. It must therefore be an explicit boolean -- `false` is
    a fine answer and a recorded one, which is the whole difference.

    Deliberately proportionate, following this power's own rule about scaling a demand to the
    cost of the thing: recording is required everywhere, but the user's signature is required
    only where the cost multiplies. One training job is a routine call; a search that launches
    twenty is not.
    """
    it = d.get("instanceType")
    if not it or not str(it).startswith("ml."):
        r.fail(
            where,
            f"instanceType is {it!r}. Record the instance the job actually ran on -- it is the "
            "largest number in the bill and the one a later reader needs in order to judge "
            "whether the next run should be bigger, smaller or the same.",
        )
    n = d.get("instanceCount")
    if not isinstance(n, int) or n < 1:
        r.fail(where, f"instanceCount is {n!r}; record how many instances ran.")

    spot = d.get("useSpot")
    if not isinstance(spot, bool):
        r.fail(
            where,
            "useSpot is not recorded as true or false. Leaving it out is not neutral: the job "
            "runs on-demand, so the expensive option gets chosen by omission and nobody sees a "
            "decision being made. `false` is a perfectly good answer -- it just has to be one.",
        )
    elif spot:
        r.note(
            f"{where} ran on Spot: cheaper, and interruptible. If a long job was interrupted "
            "and restarted, the wall-clock and the cost both moved, so compare runs on "
            "billableSeconds rather than on elapsed time."
        )


def check_amt_strategy(t: dict, r: Report) -> None:
    """AMT's four strategies are a choice too, and two of them carry hard constraints.

    Collapsing them into one `amt-search` option was a taxonomy call I made rather than
    measured, which is the same defect one level down: the tool deciding something the user
    should. They are not interchangeable, and the differences are documented rather than
    matters of taste:

      Bayesian   informed by prior runs, and "because of its sequential nature, Bayesian
                 optimization cannot massively scale"
      Random     "able to run the largest number of parallel jobs" -- runs are independent
      Hyperband  multi-fidelity with its own early stopping; "can only be used with iterative
                 algorithms ... can't be used with non-iterative algorithms"
      Grid       reproducible and exhaustive; "only categorical parameters are supported", and
                 MaxNumberOfTrainingJobs "should equal the total number of distinct categorical
                 combinations possible"

    The two constraints are checked because they are API facts: a Grid search over a continuous
    range, or Hyperband on a non-iterative algorithm, does not work rather than working badly.
    Per-region availability of each strategy was NOT verified for this power's China baseline --
    so nothing here claims a strategy is available, only that the user chose it.
    """
    STRATEGIES = {"Bayesian", "Random", "Hyperband", "Grid"}
    s = t.get("strategy")
    if s not in STRATEGIES:
        r.fail(
            "tuning",
            f"strategy is {s!r}; declare one of {sorted(STRATEGIES)} exactly as the API spells "
            "them. The four are not interchangeable -- Bayesian cannot scale parallelism, "
            "Random can, Hyperband needs an iterative algorithm, Grid is exhaustive over "
            "categoricals -- so which one runs changes both the cost and what the search can "
            "reach.",
        )
        return
    if t.get("strategyChosenBy") != "user":
        r.fail(
            "tuning",
            f"strategy is {s} and strategyChosenBy is not 'user'. Naming the method was made "
            "the user's decision for a reason, and picking among the four strategies inside it "
            "is the same decision one level down.",
        )

    space = t.get("searchSpace") or {}

    if s == "Grid":
        # AWS: only categorical parameters are supported for grid search.
        noncat = [k for k, v in space.items()
                  if not (isinstance(v, dict) and v.get("type") == "categorical"
                          and isinstance(v.get("values"), list) and v["values"])]
        if noncat:
            r.fail(
                "tuning",
                f"strategy is Grid and {sorted(noncat)} are not declared categorical. Only "
                "categorical parameters are supported by grid search, so declare each as "
                '{"type": "categorical", "values": [...]}. A continuous range under Grid does '
                "not search badly; it is rejected by the service.",
            )
        else:
            total = 1
            for v in space.values():
                total *= len(v["values"])
            mx = t.get("maxJobs")
            if isinstance(mx, int) and mx != total:
                r.fail(
                    "tuning",
                    f"strategy is Grid with {total} distinct categorical combinations and "
                    f"maxJobs is {mx}. Grid is exhaustive, so the job count is not a budget to "
                    "choose -- it equals the number of combinations. Either the space or the "
                    "cap is wrong.",
                )

    if s == "Hyperband" and t.get("iterativeAlgorithm") is not True:
        r.fail(
            "tuning",
            "strategy is Hyperband and iterativeAlgorithm is not recorded as true. Hyperband "
            "evaluates the objective after each epoch, so it works only with algorithms that "
            "run in iterations -- XGBoost and Random Cut Forest do, and a non-iterative one "
            "cannot use it at all. Record the claim explicitly; it is not inferable from a "
            "search space.",
        )

    if s == "Bayesian":
        par = t.get("maxParallelJobs")
        if isinstance(par, int) and par > 10:
            r.note(
                f"Bayesian with maxParallelJobs={par}: each run is informed by the ones before "
                "it, so high parallelism spends jobs that cannot learn from each other. Random "
                "scales further if parallelism is the point. Accounting, not a refusal -- the "
                "documented limit is that it does not scale well, not that it fails."
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
        check_amt_strategy(t, r)
        check_compute(t, r, "tuning")
        # One training job's instance is a routine call. A search multiplies it by maxJobs, which
        # is where the same decision stops being routine -- so this is the one place the compute
        # choice needs the user's name on it, for the same reason the method and the strategy do.
        if t.get("computeChosenBy") != "user":
            r.fail(
                "tuning",
                "computeChosenBy is not 'user'. An AMT search runs its instance choice "
                f"{t.get('maxJobs')} times over, so the instance type, the count and the "
                "Spot decision are a budget rather than a configuration detail. Put the "
                "total to them before spending it.",
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
