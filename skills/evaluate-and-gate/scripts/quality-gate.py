#!/usr/bin/env python3
"""Apply the quality gate: compute the verdict, write the report, exit non-zero on REFUSED.

This script DECIDES. It does not check a verdict someone else wrote, and that distinction
is the whole point of it existing. A gate whose verdict is asserted by the same process
that produced the result is not a gate; a gate that exits non-zero stops a pipeline step
whether or not anyone reads its output.

It refuses to decide at all when the ordering cannot be established. The bound is declared
at stage 6, before the model's score exists, and the gate's job is to demonstrate that
rather than to assume it -- a contract that cannot be shown to predate the predictions has
not recorded the thing that makes it meaningful.

Offline by design: it reads recorded artefacts, so it runs in CI without credentials.

Usage:
    quality-gate.py <quality-gate-contract.json> <evaluation-report.json>
                    [--out artifacts/quality-gate-report.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

NULLISH = {None, "", "null", "None", "nil", "undefined"}

LOWER_IS_BETTER = {
    "mae", "mse", "rmse", "mape", "smape", "logloss", "log_loss", "brier",
    "crps", "pinball", "wape", "medae", "msle",
}


class Refusal(Exception):
    """Raised when the gate cannot be applied at all, as opposed to failing."""


def load(path: Path) -> dict:
    if not path.is_file():
        raise Refusal(f"no such file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise Refusal(f"{path} is not valid JSON: {e}") from e


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def parse_ts(v) -> datetime | None:
    if v in NULLISH:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def check_ordering(contract: dict, ev: dict, cpath: Path, epath: Path) -> str:
    """The contract must be shown to predate the predictions.

    Prefer recorded timestamps, because a file's mtime survives neither a copy nor a
    checkout. Fall back to mtime only when both files have one, and say which was used --
    an ordering established by a weaker means should say so rather than look identical to
    one established properly.
    """
    c_at = parse_ts(contract.get("declaredAt"))
    e_at = parse_ts(ev.get("predictionsWrittenAt") or ev.get("evaluatedAt"))

    if c_at and e_at:
        if c_at >= e_at:
            raise Refusal(
                f"the contract was declared at {c_at.isoformat()}, at or after the "
                f"predictions at {e_at.isoformat()}. A bound set once the score exists "
                "is not a pre-registered bound, and no later analysis can distinguish "
                "the two."
            )
        return f"recorded timestamps ({c_at.isoformat()} < {e_at.isoformat()})"

    c_m = datetime.fromtimestamp(cpath.stat().st_mtime)
    e_m = datetime.fromtimestamp(epath.stat().st_mtime)
    if c_m >= e_m:
        raise Refusal(
            f"the contract file's mtime ({c_m.isoformat()}) is at or after the "
            f"evaluation report's ({e_m.isoformat()}), and neither file records a "
            "declaredAt. Record declaredAt in the contract at stage 6 -- a real run got "
            "the substance right and could not show it, because the file named "
            "quality-gate-contract.json was written after the evaluation it judged."
        )
    return f"file mtimes only ({c_m.isoformat()} < {e_m.isoformat()}) -- weaker evidence"


def better(a: float, b: float, metric: str) -> bool:
    return a < b if metric.lower() in LOWER_IS_BETTER else a > b


def passes(score: float, bound: float, metric: str, inclusive: bool) -> bool:
    if metric.lower() in LOWER_IS_BETTER:
        return score <= bound if inclusive else score < bound
    return score >= bound if inclusive else score > bound


def check_derivation(contract: dict, cpath: Path, bound: float, metric: str,
                     lower: bool) -> str:
    """Recompute the bound from the artefact the contract says it came from.

    `declaredAt` alone cannot carry the ordering claim, and a validation run found this
    itself: it rewrote the contract and left the old `declaredAt` in place, which would
    have forged the very evidence the timestamp exists to provide. Nothing in this script
    could have told.

    So the bound has to be reproducible. `derivedFrom` names the baseline report, the
    baseline within it, and the margin; the arithmetic is redone here. Rewriting the
    contract to a bound the model can clear now breaks that arithmetic, and it breaks it
    whatever the timestamp says -- which is the point, because the timestamp is the field
    an author controls and the baseline's own measurements are not.

    THE LIMIT, stated rather than left for someone to find: a second forgery, rewriting
    the baseline report too, defeats this. That artefact is covered by training-check.py's
    own digest rules, so the two together are harder to fake than either alone -- but
    "harder" is the honest word, not "impossible".
    """
    d = contract.get("derivedFrom")
    if not isinstance(d, dict):
        return ("not declared -- the bound rests on `declaredAt` alone, which its own "
                "author can rewrite. Add derivedFrom {artifact, baseline, marginPct} to "
                "make the bound reproducible.")

    art = d.get("artifact")
    if art in NULLISH:
        raise Refusal("derivedFrom names no artifact, so there is nothing to recompute "
                      "the bound from.")
    path = Path(art)
    if not path.is_absolute():
        path = (cpath.parent / art) if not (Path.cwd() / art).is_file() else Path(art)
    if not path.is_file():
        raise Refusal(
            f"derivedFrom names {art!r}, which is not on disk (looked at {path}). A "
            "derivation that cannot be checked is a derivation nobody checked."
        )

    src = load(path)
    baselines = src.get("baselines") or src.get("naive") or {}
    name = d.get("baseline")
    if name in NULLISH:
        raise Refusal("derivedFrom names no baseline within the artifact.")
    if name not in baselines:
        raise Refusal(
            f"derivedFrom names baseline {name!r}, which {art} does not report. It has: "
            f"{sorted(baselines) or 'nothing'}."
        )

    entry = baselines[name]
    base_score = entry.get(metric) if isinstance(entry, dict) else entry
    if not isinstance(base_score, (int, float)):
        raise Refusal(
            f"baseline {name!r} in {art} reports {base_score!r} for {metric}, not a number."
        )

    margin = d.get("marginPct")
    if not isinstance(margin, (int, float)):
        raise Refusal("derivedFrom declares no numeric marginPct, so the bound cannot be "
                      "recomputed from the baseline.")

    factor = (1 - float(margin) / 100.0) if lower else (1 + float(margin) / 100.0)
    expected = base_score * factor
    tol = max(abs(expected) * 1e-3, 1e-6)
    if abs(bound - expected) > tol:
        raise Refusal(
            f"the bound does not follow from its own declared derivation. {name} scores "
            f"{base_score} for {metric}; with a {margin}% margin the bound should be "
            f"{expected:.6f}, and the contract says {bound}. Either the contract was "
            "revised after the fact or the derivation is misdescribed -- and a bound that "
            "cannot be rederived cannot be shown to predate the score it judges, whatever "
            "declaredAt says."
        )
    return (f"recomputed: {name} {metric} {base_score} with a {margin}% margin gives "
            f"{expected:.6f}, matching the declared bound")


def apply_gate(contract: dict, ev: dict, cpath: Path, epath: Path) -> dict:
    ordering = check_ordering(contract, ev, cpath, epath)

    metric = contract.get("metric")
    bound = contract.get("bound")
    if metric in NULLISH:
        raise Refusal("the contract names no metric; a bound without one is a number.")
    if not isinstance(bound, (int, float)):
        raise Refusal(f"the contract's bound {bound!r} is not a number.")
    if "lowerIsBetter" not in contract and metric.lower() not in LOWER_IS_BETTER:
        raise Refusal(
            f"metric {metric!r} is not in the known lower-is-better set and the contract "
            "does not state lowerIsBetter. Direction is not inferable from a number, and "
            "getting it backwards produces a gate that passes what it should refuse."
        )
    lower = contract.get("lowerIsBetter", metric.lower() in LOWER_IS_BETTER)
    # The declaration point is stage 6, and training-check.py refuses there. This is the
    # enforcement point, and a gate that applies a bound nobody chose is enforcing an accident.
    # Both are needed: a contract can reach stage 10 without the baseline report that stage 6
    # checked, and then nothing would have asked.
    for field, what in (("marginChosenBy", "the margin over the baseline"),
                        ("metricChosenBy", "the primary metric")):
        if contract.get(field) != "user":
            raise Refusal(
                f"{field} is not 'user', so {what} was not the user's decision. This gate "
                "decides whether a model may be registered; applying a bound or a metric that "
                "nobody chose enforces an accident with the authority of a refusal. Record the "
                "choice in the contract at stage 6, before the score exists."
            )
    inclusive = contract.get("boundInclusive", True)
    derivation = check_derivation(contract, cpath, float(bound), metric, lower)

    measured = ev.get("metrics") or {}
    if metric not in measured:
        raise Refusal(
            f"the evaluation does not report {metric!r}, which is the primary metric the "
            f"bound is denominated in. Reported: {sorted(measured) or 'nothing'}."
        )
    score = measured[metric]
    if not isinstance(score, (int, float)):
        raise Refusal(f"the reported {metric} is {score!r}, not a number.")

    if not ev.get("recomputed"):
        raise Refusal(
            "the evaluation does not record independent recomputation. A number copied "
            "from the job that produced it is a number one implementation deep."
        )

    declared = set(contract.get("requiredMetrics") or [])
    reported = set(measured)
    missing = declared - reported
    if missing:
        recorded = {
            o.get("metric")
            for o in (ev.get("metricsOmitted") or [])
            if isinstance(o, dict) and o.get("reason") not in NULLISH
        }
        unexplained = missing - recorded
        if unexplained:
            raise Refusal(
                f"declared metrics were not reported and not explained: "
                f"{sorted(unexplained)}. An omission that is recorded is a decision; a "
                "silent one is indistinguishable from a metric that passed."
            )

    baselines = ev.get("baselines") or {}
    beaten = {}
    for name, b in baselines.items():
        if isinstance(b, (int, float)):
            beaten[name] = better(score, float(b), metric)

    verdict = "PASS" if passes(score, float(bound), metric, inclusive) else "REFUSED"

    losing = [n for n, ok in beaten.items() if not ok]
    if verdict == "PASS" and losing:
        verdict = "REFUSED"
        baseline_note = (
            f"the model is not better than {losing} on {metric}. A model that clears its "
            "bound while losing to a baseline has cleared a bar set too low, and the "
            "bound is the thing to revise -- visibly, in the contract."
        )
    else:
        baseline_note = None

    recorded_verdict = ev.get("verdict")
    if recorded_verdict not in NULLISH and str(recorded_verdict).upper() != verdict:
        raise Refusal(
            f"the evaluation report records verdict {recorded_verdict!r} but the bound "
            f"gives {verdict}. The gate computes the verdict; a disagreement means one of "
            "the two was written by hand."
        )

    report = {
        "status": verdict,
        "metric": metric,
        "measured": score,
        "bound": bound,
        "boundInclusive": inclusive,
        "lowerIsBetter": bool(lower),
        "marginToBound": round(float(bound) - score, 12) if lower else round(score - float(bound), 12),
        "baselines": baselines,
        "beatsBaseline": beaten,
        "contractDigest": digest(cpath),
        "orderingEstablishedBy": ordering,
        "boundDerivation": derivation,
        "registrationAllowed": verdict == "PASS",
        "releaseAllowed": verdict == "PASS",
    }
    if baseline_note:
        report["refusalReason"] = baseline_note
    elif verdict == "REFUSED":
        report["refusalReason"] = (
            f"{metric} {score} does not satisfy the pre-registered bound {bound}."
        )
        report["remedy"] = [
            "train or tune a model that clears the bound",
            "revise the bound in the contract, naming the change and why -- a new "
            "contract, not an edit to this one",
            "add data or inputs, then re-enter at the stage the change affects",
        ]
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("contract", type=Path)
    ap.add_argument("evaluation", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="write the gate report here")
    ap.add_argument(
        "--no-fail-on-refusal",
        action="store_true",
        help=(
            "exit 0 on REFUSED, so a pipeline step routes on the report instead of "
            "crashing. For use inside a Processing step whose successor is a ConditionStep "
            "reading registrationAllowed. The suppression is recorded in the report."
        ),
    )
    args = ap.parse_args()

    try:
        contract = load(args.contract)
        ev = load(args.evaluation)
        report = apply_gate(contract, ev, args.contract, args.evaluation)
    except Refusal as e:
        print(f"CANNOT APPLY GATE: {e}", file=sys.stderr)
        return 2

    if args.no_fail_on_refusal:
        # The suppression goes in the report, not just in the log. A ConditionStep reads
        # the report, so this is the one place a downstream reader will actually see that
        # a refusal was allowed not to stop the step. Without it the flag would quietly
        # convert a refusal into advice, which is the one thing this power's taxonomy says
        # must not happen silently.
        report["failOnRefusalSuppressed"] = True

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if report["status"] == "PASS":
        print(
            f"PASS  {report['metric']} {report['measured']} satisfies {report['bound']}; "
            f"beats {sum(report['beatsBaseline'].values())}/"
            f"{len(report['beatsBaseline'])} baselines."
        )
        print(f"      ordering: {report['orderingEstablishedBy']}")
        return 0

    print(f"REFUSED  {report['refusalReason']}", file=sys.stderr)
    for line in report.get("remedy", []):
        print(f"  fix: {line}", file=sys.stderr)
    print("  registration and release are not permitted.", file=sys.stderr)

    if args.no_fail_on_refusal:
        # A refusal that exits zero is still a refusal: registrationAllowed is false and
        # every reader of the report sees it. What changes is who acts on it. Inside a
        # Pipeline the successor should be a ConditionStep routing on that field, because
        # a metric missing its bound is a RESULT and not a crashed job -- a validation run
        # had to write its own 110-line wrapper to get this behaviour, and the wrapper was
        # right. Outside a Pipeline the exit code is what makes the refusal hold, so it
        # stays the default and this has to be asked for.
        print("  exit 0 requested (--no-fail-on-refusal); route on "
              "registrationAllowed: false.", file=sys.stderr)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
