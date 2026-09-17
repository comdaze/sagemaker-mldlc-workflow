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
    inclusive = contract.get("boundInclusive", True)

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
    args = ap.parse_args()

    try:
        contract = load(args.contract)
        ev = load(args.evaluation)
        report = apply_gate(contract, ev, args.contract, args.evaluation)
    except Refusal as e:
        print(f"CANNOT APPLY GATE: {e}", file=sys.stderr)
        return 2

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
    return 1


if __name__ == "__main__":
    sys.exit(main())
