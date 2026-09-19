#!/usr/bin/env python3
"""Fixtures for the three fixes: leakage-screen's assumption path, quality-gate's exit
behaviour, and the derivedFrom recomputation that makes a re-declared bound detectable."""
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCREEN = ROOT / "skills" / "leakage-guard" / "scripts" / "leakage-screen.py"
GATE = ROOT / "skills" / "evaluate-and-gate" / "scripts" / "quality-gate.py"
D = Path(os.environ.get("KIROCREW_SCRATCH", "/tmp")) / "fixes"
shutil.rmtree(D, ignore_errors=True)
D.mkdir(parents=True)

fails = 0


def case(label, ok, detail=""):
    global fails
    print(f"  {'PASS' if ok else 'FAIL'} {label}")
    if not ok:
        fails += 1
        if detail:
            print("      " + str(detail).replace("\n", "\n      ")[:700])


def run(script, *a):
    p = subprocess.run([sys.executable, str(script), *map(str, a)],
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


# ---------------------------------------------------------- data
# Shaped like the validated run: a target, a leak that nearly IS the target, and a
# legitimate strong predictor whose direct-prediction error lands near the gate --
# which is what the baseline-derived bound guarantees for any good column.
rng = random.Random(7)
n = 400
rows = []
for _ in range(n):
    y = rng.gauss(300, 230)
    rows.append({
        "y": y,
        "leak": y + rng.gauss(0, 2),              # corr ~0.9999
        "day_ahead": y + rng.gauss(0, 150),       # strong, legitimate, near the gate
        "noise": rng.gauss(0, 100),
    })
csv = D / "d.csv"
with csv.open("w", encoding="utf-8") as f:
    f.write("y,leak,day_ahead,noise\n")
    for r in rows:
        f.write(f"{r['y']:.6f},{r['leak']:.6f},{r['day_ahead']:.6f},{r['noise']:.6f}\n")

# The bound is derived from the strongest baseline, as stage 6 requires. day_ahead IS that
# baseline, so the bound sits just below its own score -- the circularity in one line.
mae_da = sum(abs(r["day_ahead"] - r["y"]) for r in rows) / n
bound = round(mae_da * 0.95, 4)
print(f"day_ahead as a direct prediction: MAE {mae_da:.4f}; bound = it x 0.95 = {bound}\n")

base = {"target": "y", "candidates": ["leak", "day_ahead", "noise"],
        "bounds": {"maxAbsCorrelation": 0.95, "directPredictionFloor": bound,
                   "minDirectPredictionMarginPct": 20}}


def screen(contract, tag):
    c = D / f"c-{tag}.json"
    c.write_text(json.dumps(contract), encoding="utf-8")
    o = D / f"o-{tag}.json"
    rc, out = run(SCREEN, csv, c, "--out", o)
    got = json.loads(o.read_text(encoding="utf-8")) if o.is_file() else None
    return rc, out, got


def verdict(res, name):
    for v in (res or {}).get("inputs", res.get("verdicts", []) if res else []):
        if v.get("input") == name:
            return v
    return {}


print("=== 1. over-refusal: a legitimate strong predictor ===")
rc, out, res = screen(base, "plain")
v = verdict(res, "day_ahead")
case("with no declared assumption, day_ahead is refused (unchanged behaviour)",
     v.get("refused") is True, json.dumps(v, ensure_ascii=False))

c2 = dict(base, assumedKnownAtPredictionTime={
    "day_ahead": "the day-ahead market closes the day before delivery, so this value is published by then"})
rc, out, res = screen(c2, "assumed")
v = verdict(res, "day_ahead")
case("declared as an assumption it is not refused, and the measurement is still reported",
     v.get("refused") is False and v.get("assumed") is True
     and "directPredictionMae" in v.get("measured", {}),
     json.dumps(v, ensure_ascii=False))

print("\n=== 2. the exemption must not swallow the screen ===")
c3 = dict(base, assumedKnownAtPredictionTime={"leak": "asserted knowable, with no substance"})
rc, out, res = screen(c3, "leak-assumed")
v = verdict(res, "leak")
case("an assumption on the leak does not stop the correlation refusal",
     v.get("refused") is True and any("correlation" in r for r in v.get("reasons", [])),
     json.dumps(v, ensure_ascii=False))

c4 = dict(base, assumedKnownAtPredictionTime={"__probe__": "an attempt to switch off the self-check"})
rc, out, res = screen(c4, "probe-assumed")
# A non-zero exit here means candidates were refused, which is correct; what this case
# asserts is that the self-check still fired.
case("an assumption naming __probe__ cannot disarm the self-check",
     "probe fired" in out and "DID NOT FIRE" not in out, out)

c5 = dict(base, assumedKnownAtPredictionTime=["day_ahead"])
rc, out, res = screen(c5, "list")
case("an assumption written as a list, losing the reason, is refused", rc != 0 and "REASON" in out.upper(), out)


# ---------------------------------------------------------- gate
# Real numbers from a validation run: strongest baseline (day-ahead price) MAE 51.14, a 5%
# margin, so the bound is 48.583; the model scored 56.373994.
BASE_REPORT = {"baselines": {"day-ahead price": {"mae": 51.14},
                             "same period yesterday": {"mae": 94.42},
                             "mean": {"mae": 164.47}}}
br = D / "baseline-report.json"
br.write_text(json.dumps(BASE_REPORT), encoding="utf-8")

def contract(bound, **kw):
    c = {"metric": "mae", "bound": bound, "lowerIsBetter": True,
         "declaredAt": "2026-09-16T10:00:00+08:00",
         "derivedFrom": {"artifact": str(br), "baseline": "day-ahead price",
                         "marginPct": 5},
         "marginChosenBy": "user", "metricChosenBy": "user"}
    c.update(kw)
    return c

EV = {"metrics": {"mae": 56.373994}, "recomputed": True,
      "predictionsWrittenAt": "2026-09-16T18:00:00+08:00",
      "baselines": {"day-ahead price": {"mae": 51.14}}}

def gate(c, tag, *extra):
    cp = D / f"gc-{tag}.json"; cp.write_text(json.dumps(c), encoding="utf-8")
    ep = D / f"ge-{tag}.json"; ep.write_text(json.dumps(EV), encoding="utf-8")
    op = D / f"go-{tag}.json"
    rc, out = run(GATE, cp, ep, "--out", op, *extra)
    rep = json.loads(op.read_text(encoding="utf-8")) if op.is_file() else None
    return rc, out, rep

print("\n=== 3. exit code: non-zero by default, suppressible with a trace ===")
rc, out, rep = gate(contract(51.14 * 0.95), "default")
case("REFUSED exits non-zero by default, which is what makes it hold in a shell",
     rc == 1 and rep and rep["registrationAllowed"] is False, out)

rc, out, rep = gate(contract(51.14 * 0.95), "suppressed", "--no-fail-on-refusal")
case("--no-fail-on-refusal exits 0 while registrationAllowed stays false",
     rc == 0 and rep["registrationAllowed"] is False, out)
case("the suppression is in the report, where a ConditionStep will read it",
     rep.get("failOnRefusalSuppressed") is True, json.dumps(rep)[:300])

print("\n=== 4. the bound must be recomputable from the artefact it names ===")
rc, out, rep = gate(contract(51.14 * 0.95), "derived")
case("a bound matching its derivation is recomputed and recorded",
     rep and "recomputed" in (rep.get("boundDerivation") or ""),
     (rep or {}).get("boundDerivation"))

# Widen the bound to 60 after seeing the score of 56.37, leaving declaredAt untouched --
# the forgery shape a validation run caught in itself.
rc, out, rep = gate(contract(60.0), "forged")
case("a bound widened after the fact, with declaredAt untouched, is refused",
     rc == 2 and "does not follow from its own declared derivation" in out, out[:400])

c = contract(51.14 * 0.95); c.pop("derivedFrom")
rc, out, rep = gate(c, "noderiv")
case("absent derivedFrom it does not refuse, but the report says the evidence is weaker",
     rep is not None and "declaredAt` alone" in (rep.get("boundDerivation") or ""),
     (rep or {}).get("boundDerivation"))

c = contract(51.14 * 0.95)
c["derivedFrom"]["baseline"] = "same period yesterday"
rc, out, rep = gate(c, "wrongbase")
case("a derivation naming a different baseline, so the arithmetic fails, is refused", rc == 2, out[:300])

print("\n=== 5. the tuning method is the user's choice, and both must be possible ===")
H = "ab" * 32

# The bound is DERIVED, so the fixture derives it. Written as a literal it was correct and
# hand-maintained: 140.0 x (1 - 1.0/100) = 138.6, a relationship invisible to a reader and to
# anyone editing the margin. `check_bound` recomputes it, so a typo there fails as though the
# CODE were wrong. The same shape has already cost time twice in these suites -- a row count
# that did not reconcile, and a field emptied that the checker does not read.
STRONGEST, MARGIN_PCT = 140.0, 1.0
BOUND = STRONGEST * (1 - MARGIN_PCT / 100)

BASE_R = {"metric": "mae",
          "computed": [{"name": "mean", "score": 180.0},
                       {"name": "yest", "score": STRONGEST}],
          "strongest": "yest",
          "quality": {"metric": "mae", "bound": BOUND, "marginPct": MARGIN_PCT,
                      "derivedFrom": "yest", "contract": "contracts/q.json",
                      "marginChosenBy": "user", "metricChosenBy": "user"}}
TRAIN_R = {"image": "123.dkr.ecr.cn-north-1.amazonaws.com.cn/xgboost:1.7",
           "imageDigest": f"sha256:{H}", "inputDigests": {"train": f"sha256:{H}"},
           "channels": ["train", "validation"], "modelArtefact": "s3://b/model.tar.gz",
           "resolvedHyperparameters": {"eta": 0.1}, "billableSeconds": 134,
           "instanceType": "ml.m5.xlarge", "instanceCount": 2, "useSpot": False,
           "algorithm": "xgboost", "algorithmChosenBy": "user",
           "algorithmAlternatives": ["xgboost", "linear-learner"],
           "runtimeMode": "built-in"}
COMMON = {"budget": "20 jobs", "selectOn": "validation",
          "channels": ["train", "validation"],
          "winnerFixedBeforeTestAccess": True, "winner": {"eta": 0.1},
          "instanceType": "ml.m5.xlarge", "instanceCount": 2, "useSpot": False,
          "computeChosenBy": "user"}
FIXED = {**COMMON, "method": "fixed-candidates", "methodChosenBy": "user",
         "candidates": [{"eta": 0.1}, {"eta": 0.3}], "candidatesRun": 2}
AMT = {**COMMON, "method": "amt-search", "methodChosenBy": "user",
       "searchSpace": {"eta": [0.01, 0.3]}, "maxJobs": 20, "candidatesRun": 20,
       "strategy": "Random", "strategyChosenBy": "user"}
GRID_SPACE = {"eta": {"type": "categorical", "values": [0.01, 0.1, 0.3]},
              "depth": {"type": "categorical", "values": [3, 6]}}


def tuning(doc, tag):
    b = D / f"tb-{tag}.json"; b.write_text(json.dumps(BASE_R), encoding="utf-8")
    t = D / f"tt-{tag}.json"; t.write_text(json.dumps(TRAIN_R), encoding="utf-8")
    u = D / f"tu-{tag}.json"; u.write_text(json.dumps(doc), encoding="utf-8")
    return run(ROOT / "skills/train-and-tune/scripts/training-check.py", b, t, u)


rc, out = tuning(FIXED, "fixed")
case("fixed-candidates chosen by the user passes", rc == 0, out)
rc, out = tuning(AMT, "amt")
case("amt-search chosen by the user passes too", rc == 0, out)

# The shape a real run produced: a fixed candidate list and no record of anyone choosing it.
# The checker used to accept this AND to require `candidates`, so it was steering the answer.
silent = {k: v for k, v in FIXED.items() if k not in ("method", "methodChosenBy")}
rc, out = tuning(silent, "silent")
case("a method nobody chose is refused", rc != 0 and "methodChosenBy" in out, out)
rc, out = tuning({**AMT, "candidatesRun": 45}, "over")
case("an amt search past its maxJobs is refused",
     rc != 0 and "maxJobs" in out, out)
rc, out = tuning({k: v for k, v in AMT.items() if k != "searchSpace"}, "nospace")
case("amt-search with no declared searchSpace is refused",
     rc != 0 and "searchSpace" in out, out)

print("\n=== 6. and the AMT strategy is a choice one level down ===")
# Two of the four carry constraints that are API facts rather than preferences, so they are
# refusals: Grid accepts only categorical parameters and its job count equals the number of
# combinations; Hyperband works only with iterative algorithms.
rc, out = tuning({k: v for k, v in AMT.items() if k != "strategyChosenBy"}, "nostrat")
case("a strategy nobody chose is refused", rc != 0 and "strategyChosenBy" in out, out)
rc, out = tuning({**AMT, "strategy": "Sensible"}, "badstrat")
case("a strategy outside the four is refused", rc != 0 and "declare one of" in out, out)
rc, out = tuning({**AMT, "strategy": "Hyperband"}, "hyper")
case("Hyperband without iterativeAlgorithm is refused",
     rc != 0 and "iterativeAlgorithm" in out, out)
rc, out = tuning({**AMT, "strategy": "Hyperband", "iterativeAlgorithm": True}, "hyperok")
case("Hyperband declaring an iterative algorithm passes", rc == 0, out)
rc, out = tuning({**AMT, "strategy": "Grid", "maxJobs": 6, "candidatesRun": 6}, "gridcont")
case("Grid over a continuous range is refused",
     rc != 0 and "not declared categorical" in out, out)
rc, out = tuning({**AMT, "strategy": "Grid", "searchSpace": GRID_SPACE,
                  "maxJobs": 4, "candidatesRun": 4}, "gridcount")
case("Grid whose maxJobs is not the combination count is refused",
     rc != 0 and "distinct categorical combinations" in out, out)
rc, out = tuning({**AMT, "strategy": "Grid", "searchSpace": GRID_SPACE,
                  "maxJobs": 6, "candidatesRun": 6}, "gridok")
case("Grid with 3x2 categoricals and maxJobs 6 passes", rc == 0, out)

print("\n=== 7. the compute decision is recorded, and Spot is explicit ===")


def training(doc, tag):
    b = D / f"cb-{tag}.json"; b.write_text(json.dumps(BASE_R), encoding="utf-8")
    tr = D / f"ct-{tag}.json"; tr.write_text(json.dumps(doc), encoding="utf-8")
    u = D / f"cu-{tag}.json"; u.write_text(json.dumps(FIXED), encoding="utf-8")
    return run(ROOT / "skills/train-and-tune/scripts/training-check.py", b, tr, u)


rc, out = training(TRAIN_R, "ok")
case("a record naming instance, count and useSpot passes", rc == 0, out)
rc, out = training({k: v for k, v in TRAIN_R.items() if k != "instanceType"}, "noinst")
case("no instanceType is refused", rc != 0 and "instanceType" in out, out)
rc, out = training({k: v for k, v in TRAIN_R.items() if k != "useSpot"}, "nospot")
case("useSpot left out is refused, not read as on-demand",
     rc != 0 and "not neutral" in out, out)
rc, out = training({**TRAIN_R, "instanceCount": 0}, "zero")
case("an instanceCount of zero is refused", rc != 0 and "instanceCount" in out, out)
rc, out = tuning({k: v for k, v in AMT.items() if k != "computeChosenBy"}, "nocompute")
case("an amt search whose compute nobody signed off is refused",
     rc != 0 and "computeChosenBy" in out, out)
rc, out = tuning({k: v for k, v in FIXED.items() if k != "computeChosenBy"}, "fixednosign")
case("a single fixed run does NOT need that signature", rc == 0, out)

print("\n=== 8. the algorithm is the user's choice, and a menu of one is not a menu ===")
rc, out = training(TRAIN_R, "algo-ok")
case("a named algorithm, chosen by the user, with alternatives, passes", rc == 0, out)
rc, out = training({k: v for k, v in TRAIN_R.items() if k != "algorithm"}, "algo-none")
case("no algorithm recorded is refused", rc != 0 and "no algorithm" in out, out)
rc, out = training({k: v for k, v in TRAIN_R.items() if k != "algorithmChosenBy"}, "algo-nosign")
case("an algorithm nobody chose is refused",
     rc != 0 and "algorithmChosenBy" in out, out)
rc, out = training({k: v for k, v in TRAIN_R.items() if k != "algorithmAlternatives"},
                   "algo-noalts")
case("a choice with no alternatives recorded is refused",
     rc != 0 and "not a choice" in out, out)
rc, out = training({**TRAIN_R, "algorithmAlternatives": ["xgboost"]}, "algo-self")
case("alternatives listing only the choice itself is refused",
     rc != 0 and "menu with one item" in out, out)
rc, out = training({**TRAIN_R, "runtimeMode": "magic"}, "algo-mode")
case("an unknown runtimeMode is refused", rc != 0 and "runtimeMode" in out, out)

print("\n=== 9. the ship/do-not-ship line and the definition of good are the user's ===")
Q = BASE_R["quality"]
rc, out = training(TRAIN_R, "sig-ok")
case("a bound whose margin and metric the user chose passes", rc == 0, out)


def baseline_missing(field, tag):
    b = {**BASE_R, "quality": {k: v for k, v in Q.items() if k != field}}
    bp = D / f"sb-{tag}.json"; bp.write_text(json.dumps(b), encoding="utf-8")
    tp = D / f"st-{tag}.json"; tp.write_text(json.dumps(TRAIN_R), encoding="utf-8")
    return run(ROOT / "skills/train-and-tune/scripts/training-check.py", bp, tp)


rc, out = baseline_missing("marginChosenBy", "nomargin")
case("a margin nobody chose is refused at the stage it is declared",
     rc != 0 and "marginChosenBy" in out, out)
rc, out = baseline_missing("metricChosenBy", "nometric")
case("a primary metric nobody chose is refused", rc != 0 and "metricChosenBy" in out, out)

# The gate enforces the bound, so it must refuse an unsigned contract too -- a contract can
# reach stage 10 without the baseline report that stage 6 checked.
c = {k: v for k, v in contract(51.14 * 0.95).items() if k != "marginChosenBy"}
rc, out, rep = gate(c, "unsigned")
case("the gate refuses to enforce a bound nobody chose",
     rc == 2 and "marginChosenBy" in out, out)

print("\n=== 10. what data is in, what is out, and where the cuts fall ===")
# The counts have to reconcile -- check_counts adds the partitions to filteredOutCount and
# compares the total against the manifest -- so the fixture DERIVES them rather than asserting
# three numbers that must happen to add up. An earlier version had them not adding up, and the
# refusal that produced looked like a bug in the new rule rather than arithmetic in the fixture.
H2 = "ab" * 32
SAMPLES, TEST_ROWS = 34944, 4944
MAN = {"uri": "s3://b/d.csv", "versionId": "v1", "etag": '"e"',
       "digest": f"sha256:{H2}", "sampleCount": SAMPLES, "readBackVerified": True,
       "completeness": {"asserted": True},
       "scope": {"included": "2024 in full", "excluded": [], "scopeChosenBy": "user"}}


def report_for(filtered: int = 0, **extra) -> dict:
    """A processing report whose partitions reconcile with the manifest by construction."""
    rep = {"completeness": {"asserted": True, "unit": "delivery day", "expectedPerUnit": 96,
                            "passed": True},
           "partitions": {"train": {"sampleCount": SAMPLES - TEST_ROWS - filtered,
                                    "from": "2024-01-01", "to": "2024-10-31"},
                          "test": {"sampleCount": TEST_ROWS,
                                   "from": "2024-11-01", "to": "2024-12-31"}},
           "filteredOutCount": filtered, "fittedArtefacts": [],
           "outputs": [{"name": "train", "uri": "s3://b/train.csv",
                        "digest": f"sha256:{H2}"}],
           "splitChosenBy": "user"}
    rep.update(extra)
    return rep


REP = report_for()
CC = ROOT / "skills/data-pipeline/scripts/contract-check.py"


def contracts(man, rep, tag):
    mp = D / f"cm-{tag}.json"; mp.write_text(json.dumps(man), encoding="utf-8")
    if rep is None:
        return run(CC, mp)
    rp = D / f"cr-{tag}.json"; rp.write_text(json.dumps(rep), encoding="utf-8")
    return run(CC, mp, rp)


rc, out = contracts(MAN, REP, "ok")
case("a manifest and report naming scope and split pass", rc == 0, out)
rc, out = contracts({k: v for k, v in MAN.items() if k != "scope"}, None, "noscope")
case("a manifest with no scope is refused", rc != 0 and "no scope" in out, out)
m = {**MAN, "scope": {"included": "2024", "scopeChosenBy": "user"}}
rc, out = contracts(m, None, "noexcl")
case("an absent excluded list is refused; empty is not the same as missing",
     rc != 0 and "not the same as empty" in out, out)
m = {**MAN, "scope": {"included": "2024", "excluded": [{"what": "2026 CSV"}],
                      "scopeChosenBy": "user"}}
rc, out = contracts(m, None, "noreason")
case("an exclusion with no reason is refused", rc != 0 and "records no reason" in out, out)
m = {**MAN, "scope": {**MAN["scope"], "scopeChosenBy": "agent"}}
rc, out = contracts(m, None, "notuser")
case("a scope the user did not choose is refused",
     rc != 0 and "scopeChosenBy" in out, out)
rc, out = contracts(MAN, {k: v for k, v in REP.items() if k != "splitChosenBy"}, "nosplit")
case("boundaries nobody placed are refused", rc != 0 and "splitChosenBy" in out, out)
rc, out = contracts(MAN, {**REP, "filteredOutCount": 120}, "nofilt")
case("rows filtered out with no reason are refused",
     rc != 0 and "filteredOutReason" in out, out)
rc, out = contracts(MAN, report_for(filtered=120,
                                   filteredOutReason="target missing"), "filt")
case("filtered rows with a reason pass, counts still reconciling", rc == 0, out)

# A real run wrote this field as a dict, and `x in NULLISH` raises TypeError on an unhashable x.
# contract-check crashed with a traceback instead of judging -- a gate that raises gates nothing,
# and the traceback reads as a bug in the power rather than as anything about the data. All 39 of
# those tests now route through is_blank(); this case is the one that found it.
rc, out = contracts(MAN, report_for(filtered=120,
                                   filteredOutReason={"rows": 120, "why": "target missing"}),
                    "filt-dict")
case("an unhashable reason is judged, not crashed on",
     rc == 0 and "Traceback" not in out, out)
rc, out = contracts(MAN, report_for(filtered=120, filteredOutReason={}), "filt-empty")
case("an EMPTY container still counts as no reason",
     rc != 0 and "filteredOutReason" in out and "Traceback" not in out, out)

print(f"\n{'all passed' if not fails else f'{fails} failed'}")
sys.exit(1 if fails else 0)
