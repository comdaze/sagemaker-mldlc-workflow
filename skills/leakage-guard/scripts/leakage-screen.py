#!/usr/bin/env python3
"""Screen candidate inputs against the target, and refuse the ones that nearly are it.

This is the only script in this power that computes on DATA rather than checking a
record, and it has to be: for leakage the measurement IS the refusal. A record saying
"the screen passed" is worth nothing unless something ran the comparison.

STANDARD LIBRARY ONLY, on purpose. This has to run wherever the data is -- inside a
processing job, inside a built-in algorithm image, on a laptop -- and those environments
do not reliably have pandas or numpy. Pearson correlation, a rank-based AUC and a mean
absolute error are forty lines each; requiring a dependency to compute them would put the
screen out of reach in exactly the places it belongs.

IT PROVES ITSELF ON EVERY RUN. Before reporting anything, it constructs a probe from the
target -- the target itself, lightly corrupted -- pushes it through the same code path, and
REFUSES TO REPORT AT ALL if the screen does not fire on it. A screen that has never
refused anything is indistinguishable from a screen with a sign error, and both report
PASS. The probe is what tells them apart, so it is not optional and not the caller's job.

Usage:
    leakage-screen.py <data.csv> <contract.json> [--out artifacts/leakage-audit.json]

The contract declares the target, the candidate inputs, and the bounds:

    {"target": "y",
     "candidates": ["a", "b", "c"],
     "bounds": {"maxAbsCorrelation": 0.95,
                "maxSingleInputAuc": 0.95,
                "directPredictionFloor": 48.59}}

`directPredictionFloor` is the quality gate's bound: a candidate whose error used
directly as the prediction lands at or below it is refused, because if one input already
clears the bar the model must clear, the model is not the thing being measured.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from pathlib import Path

NULLISH = {"", "na", "nan", "null", "none", "-", "?"}


class Refusal(Exception):
    """The screen cannot be run, as distinct from a candidate being refused."""


# ---------------------------------------------------------------- statistics


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def mae(pred: list[float], actual: list[float]) -> float:
    return sum(abs(p - a) for p, a in zip(pred, actual)) / len(pred)


def auc(scores: list[float], labels: list[int]) -> float | None:
    """Rank-based AUC (Mann-Whitney U), ties averaged. Stdlib only."""
    pos = sum(labels)
    neg = len(labels) - pos
    if pos == 0 or neg == 0:
        return None

    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1  # 1-based, ties averaged
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1

    rank_sum_pos = sum(r for r, l in zip(ranks, labels) if l == 1)
    a = (rank_sum_pos - pos * (pos + 1) / 2) / (pos * neg)
    # A single input predicting the target INVERSELY is just as much of a leak, so
    # report the distance from chance rather than the raw direction.
    return max(a, 1 - a)


# ---------------------------------------------------------------- data


def read_columns(path: Path) -> dict[str, list[str]]:
    if not path.is_file():
        raise Refusal(f"no such file: {path}")
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise Refusal(f"{path} has no data rows")
    cols: dict[str, list[str]] = {k: [] for k in rows[0]}
    for row in rows:
        for k in cols:
            cols[k].append(row.get(k) or "")
    return cols


def numeric_pairs(a: list[str], b: list[str]) -> tuple[list[float], list[float]]:
    """Rows where both values parse as numbers. Pairwise-complete, not row-complete."""
    xs: list[float] = []
    ys: list[float] = []
    for u, v in zip(a, b):
        if u.strip().lower() in NULLISH or v.strip().lower() in NULLISH:
            continue
        try:
            xs.append(float(u))
            ys.append(float(v))
        except ValueError:
            continue
    return xs, ys


def as_binary(vals: list[str]) -> list[int] | None:
    seen = sorted({v.strip() for v in vals if v.strip().lower() not in NULLISH})
    if len(seen) != 2:
        return None
    lo, hi = seen
    return [1 if v.strip() == hi else 0 for v in vals]


# ---------------------------------------------------------------- the screen


def screen_one(
    name: str, values: list[str], target: list[str], bounds: dict
) -> dict:
    """One candidate against the target. Returns a verdict dict."""
    out: dict = {"input": name, "refused": False, "reasons": [], "measured": {}}

    xs, ys = numeric_pairs(values, target)
    binary = as_binary(target)

    if not xs and binary is None:
        out["reasons"].append(
            "not screenable by these statistics: neither pair is numeric and the target "
            "is not binary. Screen it by the checks in references/screening-unstructured.md "
            "and record the result -- an unscreened candidate is not a passed one."
        )
        out["screenable"] = False
        return out

    out["screenable"] = True

    if len(xs) >= 3:
        r = pearson(xs, ys)
        if r is not None:
            out["measured"]["absCorrelation"] = round(abs(r), 6)
            b = bounds.get("maxAbsCorrelation")
            if b is not None and abs(r) > b:
                out["refused"] = True
                out["reasons"].append(
                    f"|correlation| {abs(r):.6f} exceeds the declared bound {b}"
                )

        floor = bounds.get("directPredictionFloor")
        if floor is not None:
            e = mae(xs, ys)
            out["measured"]["directPredictionMae"] = round(e, 6)
            if e <= floor:
                out["refused"] = True
                out["reasons"].append(
                    f"used directly as the prediction it scores MAE {e:.6f}, at or "
                    f"below the quality bound {floor}. If one input already clears the "
                    "bar the model must clear, the model is not what is being measured."
                )

    if binary is not None and len(xs) >= 3:
        # Score the candidate against the binary target on rows where it parses.
        pairs = [
            (float(v), l)
            for v, l in zip(values, binary)
            if v.strip().lower() not in NULLISH and _is_num(v)
        ]
        if pairs:
            a = auc([p for p, _ in pairs], [l for _, l in pairs])
            if a is not None:
                out["measured"]["singleInputAuc"] = round(a, 6)
                b = bounds.get("maxSingleInputAuc")
                if b is not None and a > b:
                    out["refused"] = True
                    out["reasons"].append(
                        f"single-input AUC {a:.6f} exceeds the declared bound {b}"
                    )

    return out


def _is_num(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def build_probe(target: list[str], seed: int = 0) -> tuple[list[str], str] | None:
    """An input known to be inadmissible, built from the target itself.

    Numeric target: the target plus 1% noise. Binary target: the label with 1% flipped.
    Both are ~the target, so any working screen must refuse them.
    """
    rng = random.Random(seed)
    nums = [float(v) for v in target if v.strip().lower() not in NULLISH and _is_num(v)]
    if len(nums) >= 3:
        mean = sum(nums) / len(nums)
        var = sum((x - mean) ** 2 for x in nums) / len(nums)
        sd = math.sqrt(var)
        if sd > 0:
            out = []
            for v in target:
                if v.strip().lower() in NULLISH or not _is_num(v):
                    out.append("")
                else:
                    out.append(str(float(v) + rng.gauss(0, sd * 0.01)))
            return out, "target plus 1% noise"

    binary = as_binary(target)
    if binary is not None:
        seen = sorted({v.strip() for v in target if v.strip().lower() not in NULLISH})
        lo, hi = seen
        out = []
        for v, l in zip(target, binary):
            if v.strip().lower() in NULLISH:
                out.append("")
            elif rng.random() < 0.01:
                out.append(lo if l == 1 else hi)
            else:
                out.append(v.strip())
        return out, "label with 1% of values flipped"

    return None


def run(data: dict[str, list[str]], contract: dict) -> dict:
    tname = contract.get("target")
    if not tname:
        raise Refusal("the contract names no target")
    if tname not in data:
        raise Refusal(f"target {tname!r} is not a column in the data")

    bounds = contract.get("bounds") or {}
    if not bounds:
        raise Refusal(
            "the contract declares no bounds. A screen with no bound cannot refuse, and "
            "the bound belongs in the contract so that raising it is a reviewable act."
        )

    target = data[tname]
    candidates = contract.get("candidates")
    if not candidates:
        candidates = [c for c in data if c != tname]
    missing = [c for c in candidates if c not in data]
    if missing:
        raise Refusal(f"declared candidates absent from the data: {missing}")

    # --- the self-test, before anything is reported ---
    probe = build_probe(target)
    if probe is None:
        raise Refusal(
            "cannot build a probe from this target, so the screen cannot be shown to "
            "work on this data. Construct a known-inadmissible input by hand and verify "
            "the refusal before trusting any PASS from this run."
        )
    probe_values, probe_kind = probe
    probe_verdict = screen_one("__probe__", probe_values, target, bounds)
    if not probe_verdict["refused"]:
        raise Refusal(
            f"THE SCREEN DID NOT FIRE ON ITS OWN PROBE ({probe_kind}). The probe is "
            f"nearly the target, so a working screen must refuse it. Measured: "
            f"{probe_verdict['measured']}. Every PASS from this run would be "
            "meaningless -- fix the bounds or the statistics before reading further."
        )

    verdicts = [screen_one(c, data[c], target, bounds) for c in candidates]
    refused = [v for v in verdicts if v["refused"]]
    unscreenable = [v for v in verdicts if not v.get("screenable")]

    return {
        "target": tname,
        "bounds": bounds,
        "rows": len(target),
        "candidatesScreened": len(verdicts),
        "probe": {
            "kind": probe_kind,
            "refused": True,
            "measured": probe_verdict["measured"],
            "note": "the screen was verified against a known-inadmissible input on this "
                    "data, in this run, before any verdict below was reported",
        },
        "verdicts": verdicts,
        "refusedCount": len(refused),
        "unscreenableCount": len(unscreenable),
        "status": "REFUSED" if refused else "PASS",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("data", type=Path, help="CSV containing the target and candidates")
    ap.add_argument("contract", type=Path, help="JSON declaring target, candidates, bounds")
    ap.add_argument("--out", type=Path, default=None, help="write the audit artefact here")
    args = ap.parse_args()

    try:
        data = read_columns(args.data)
        contract = json.loads(args.contract.read_text(encoding="utf-8"))
        report = run(data, contract)
    except Refusal as e:
        print(f"CANNOT SCREEN: {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"CANNOT SCREEN: {args.contract} is not valid JSON: {e}", file=sys.stderr)
        return 2

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(
        f"probe fired ({report['probe']['kind']}): "
        f"{report['probe']['measured']} -- the screen works on this data"
    )
    for v in report["verdicts"]:
        if not v.get("screenable"):
            print(f"UNSCREENED  {v['input']}: {v['reasons'][0]}")

    if report["status"] == "PASS":
        print(
            f"PASS  {report['candidatesScreened']} candidate(s) screened against "
            f"{report['target']}, none refused."
        )
        if report["unscreenableCount"]:
            print(
                f"      {report['unscreenableCount']} not screenable by these "
                "statistics -- screen them by modality and record the result."
            )
        return 0

    print(f"\nREFUSED  {report['refusedCount']} candidate(s):", file=sys.stderr)
    for v in report["verdicts"]:
        if v["refused"]:
            print(f"  {v['input']}", file=sys.stderr)
            for reason in v["reasons"]:
                print(f"    {reason}", file=sys.stderr)
    print(
        "\n  An input that nearly is the target is either the target under another name "
        "or computed from it.\n  Move it to the forbidden list, or -- if it is genuinely "
        "available at prediction time --\n  raise the bound in the contract, where the "
        "change is visible.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
