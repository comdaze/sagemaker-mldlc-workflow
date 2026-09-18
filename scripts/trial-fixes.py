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
    print(f"  {'✔' if ok else '✘'} {label}")
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
print(f"day_ahead 直接预测 MAE {mae_da:.4f}；门限 = 它 × 0.95 = {bound}\n")

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


print("=== 1. 过度拒绝：合法强预测列 ===")
rc, out, res = screen(base, "plain")
v = verdict(res, "day_ahead")
case("没有声明假设时，day_ahead 被拒（保留原行为）",
     v.get("refused") is True, json.dumps(v, ensure_ascii=False))

c2 = dict(base, assumedKnownAtPredictionTime={
    "day_ahead": "日前市场在交割日前一天收盘，交割时该值已公布"})
rc, out, res = screen(c2, "assumed")
v = verdict(res, "day_ahead")
case("声明了假设后不再被拒，但测量值仍在报告里",
     v.get("refused") is False and v.get("assumed") is True
     and "directPredictionMae" in v.get("measured", {}),
     json.dumps(v, ensure_ascii=False))

print("\n=== 2. 逃逸口不能吞掉整个筛查 ===")
c3 = dict(base, assumedKnownAtPredictionTime={"leak": "我说它可知"})
rc, out, res = screen(c3, "leak-assumed")
v = verdict(res, "leak")
case("对 leak 声明假设，相关性那条仍然拒绝",
     v.get("refused") is True and any("correlation" in r for r in v.get("reasons", [])),
     json.dumps(v, ensure_ascii=False))

c4 = dict(base, assumedKnownAtPredictionTime={"__probe__": "试图关掉自证"})
rc, out, res = screen(c4, "probe-assumed")
# 退出码非零是候选被拒，那是对的；这里要断言的是自证仍然开火。
case("对 __probe__ 声明假设，关不掉自证",
     "probe fired" in out and "DID NOT FIRE" not in out, out)

c5 = dict(base, assumedKnownAtPredictionTime=["day_ahead"])
rc, out, res = screen(c5, "list")
case("假设写成列表（理由丢失）被拒", rc != 0 and "REASON" in out.upper(), out)


# ---------------------------------------------------------- gate
# 第九轮的真实数字：最强基线 日前电价 MAE 51.14，5% 余量 → 界 48.583；模型 56.373994。
BASE_REPORT = {"baselines": {"day-ahead price": {"mae": 51.14},
                             "same period yesterday": {"mae": 94.42},
                             "mean": {"mae": 164.47}}}
br = D / "baseline-report.json"
br.write_text(json.dumps(BASE_REPORT), encoding="utf-8")

def contract(bound, **kw):
    c = {"metric": "mae", "bound": bound, "lowerIsBetter": True,
         "declaredAt": "2026-09-16T10:00:00+08:00",
         "derivedFrom": {"artifact": str(br), "baseline": "day-ahead price",
                         "marginPct": 5}}
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

print("\n=== 3. 退出码：默认非零，pipeline 里可要求为零但留痕 ===")
rc, out, rep = gate(contract(51.14 * 0.95), "default")
case("默认 REFUSED 退出非零（shell 里拒绝才成立）",
     rc == 1 and rep and rep["registrationAllowed"] is False, out)

rc, out, rep = gate(contract(51.14 * 0.95), "suppressed", "--no-fail-on-refusal")
case("--no-fail-on-refusal 退出 0，但 registrationAllowed 仍为 false",
     rc == 0 and rep["registrationAllowed"] is False, out)
case("抑制这件事写进了报告，ConditionStep 读得到",
     rep.get("failOnRefusalSuppressed") is True, json.dumps(rep)[:300])

print("\n=== 4. 界必须能从它命名的产物重算出来 ===")
rc, out, rep = gate(contract(51.14 * 0.95), "derived")
case("界与推导一致 → 重算通过并记录",
     rep and "recomputed" in (rep.get("boundDerivation") or ""),
     (rep or {}).get("boundDerivation"))

# 看过分数（56.37）后把界改宽到 60，declaredAt 保持不动 —— 正是第十轮自己发现的伪造形状
rc, out, rep = gate(contract(60.0), "forged")
case("事后改宽界（declaredAt 不动）被拒",
     rc == 2 and "does not follow from its own declared derivation" in out, out[:400])

c = contract(51.14 * 0.95); c.pop("derivedFrom")
rc, out, rep = gate(c, "noderiv")
case("没有 derivedFrom 时不拒绝，但报告说清证据更弱",
     rep is not None and "declaredAt` alone" in (rep.get("boundDerivation") or ""),
     (rep or {}).get("boundDerivation"))

c = contract(51.14 * 0.95)
c["derivedFrom"]["baseline"] = "same period yesterday"
rc, out, rep = gate(c, "wrongbase")
case("推导指向另一个基线（算术不成立）被拒", rc == 2, out[:300])

print(f"\n{'全部通过' if not fails else f'{fails} 项未通过'}")
sys.exit(1 if fails else 0)
