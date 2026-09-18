#!/usr/bin/env python3
"""Break next.py and report.py one condition at a time, and check the refusal is the
intended one rather than merely some refusal."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

S = Path(sys.argv[1]).resolve()          # scripts dir
D = Path(os.environ.get("KIROCREW_SCRATCH", "/tmp")) / "ctrl"
shutil.rmtree(D, ignore_errors=True)
D.mkdir(parents=True)

GOOD = """# Plan

PARTITION: aws-cn
SDK: 3.22.0
PRESET: data-prep-only
APPROVED: "approved, go with data-prep-only" @ 2026-09-18T13:05:00+08:00
LAST_DONE: 2 @ 2026-09-18T10:00:00+08:00

1. [x] **Frame the problem** _(Stage: 1 | Skill: ml-planning)_
2. [x] **Environment readiness** _(Stage: 2 | Skill: ml-planning)_
3. [ ] **Register the dataset** _(Stage: 3 | Skill: data-pipeline)_
4. [ ] **Leakage guard** _(Stage: 4 | Skill: leakage-guard)_
5. [ ] **Data processing** _(Stage: 5 | Skill: data-pipeline)_
"""

UNAPPROVED = GOOD.replace(
    'APPROVED: "approved, go with data-prep-only" @ 2026-09-18T13:05:00+08:00\n', "")

TRUNC = GOOD.replace("PRESET: data-prep-only", "PRESET: full-lifecycle")


def run(script, *a):
    p = subprocess.run([sys.executable, str(S / script), *map(str, a)],
                       capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def case(label, want, rc, out, needle=None):
    got = "REFUSED" if rc else "OK"
    ok = got == want and (needle is None or needle in out)
    print(f"  {'PASS' if ok else 'FAIL'} {label:<46} {got}")
    if not ok:
        print("      " + out.replace("\n", "\n      ")[:600])
    return ok

fails = 0
plan = D / "PLAN.md"

print("=== approval gates execution ===")
pu = D / "unapproved.md"; pu.write_text(UNAPPROVED, encoding="utf-8")
rc, out = run("next.py", pu)
fails += not case("unapproved: present-plan is the only directive", "OK", rc, out, "present-plan")
no_exec = "execute" not in out
print(f"  {'PASS' if no_exec else 'FAIL'} unapproved: nothing is dispatched to execute")
fails += not no_exec

print("\n=== dispatch ===")
plan.write_text(GOOD, encoding="utf-8")
rc, out = run("next.py", plan)
fails += not case("a clean plan gets a directive", "OK", rc, out, "task")
print("      " + out.replace("\n", "\n      "))

print("\n=== a preset whose tail stages have no task ===")
p2 = D / "trunc.md"; p2.write_text(TRUNC, encoding="utf-8")
rc, out = run("next.py", p2)
fails += not case("stages 6-16 absent: a repair-plan directive, not a violation list", "OK", rc, out, "repair-plan")
led = (D / "PLAN.state.json")
rc2, out2 = run("next.py", p2)
fails += not case("repairing the plan is what was asked for, so it is not tampering", "OK", rc2, out2, "repair-plan")

print("\n=== report.py refuses by execution class ===")
plan.write_text(GOOD, encoding="utf-8"); run("next.py", plan)
rc, out = run("report.py", "--plan", plan, "--task", "4", "--state", "S",
              "--reason", "no time")
fails += not case("[S] on an ALWAYS stage", "REFUSED", rc, out, "ALWAYS")

rc, out = run("report.py", "--plan", plan, "--task", "3", "--state", "x")
fails += not case("[x] with no artefact on disk", "REFUSED", rc, out, "produces")

rc, out = run("report.py", "--plan", plan, "--task", "3", "--state", ">",
              "--execution", "arn:fake")
fails += not case("[>] on an inline-mode stage", "REFUSED", rc, out, "mode")

rc, out = run("report.py", "--plan", plan, "--task", "3", "--state", "?")
fails += not case("[?] without --asked", "REFUSED", rc, out, "asked")

print("\n=== record, then advance ===")
art = D / "artifacts"; art.mkdir(exist_ok=True)
(art / "dataset-manifest.json").write_text("{}", encoding="utf-8")
rc, out = run("report.py", "--plan", plan, "--task", "3", "--state", "x",
              "--artifact", art / "dataset-manifest.json")
fails += not case("[x] with its artefact is recorded", "OK", rc, out, "RECORDED")
rc, out = run("next.py", plan)
fails += not case("the next directive names task 4", "OK", rc, out, "task")
print("      " + [l for l in out.splitlines() if "task" in l][0].strip() if not rc else "")

print("\n=== a hand edit does not count ===")
# The hand edit here must be lint-clean, or lint catches it first and the digest path
# never runs -- which is how the first version of this case proved nothing. Moving task 4
# to [-] is legal: nothing unsettled above it, and LAST_DONE still names the highest [x].
t = plan.read_text(encoding="utf-8").replace(
    "4. [ ] **Leakage guard**", "4. [-] **Leakage guard**")
plan.write_text(t, encoding="utf-8")
rc, out = run("next.py", plan)
fails += not case("a lint-clean hand edit still fails the ledger digest", "REFUSED", rc, out, "does not count")
print("      " + out.replace("\n", "\n      ")[:340])
rc2, out2 = run("next.py", plan)
fails += not case("and it stays refused; the mismatch does not heal itself", "REFUSED", rc2, out2, "digest" if "digest" in out2 else "ledger")


print("\n=== a bad write is rolled back ===")
plan.write_text(GOOD.replace("LAST_DONE: 2", "LAST_DONE: 2"), encoding="utf-8")
run("next.py", plan)
before = plan.read_text(encoding="utf-8")
rc, out = run("report.py", "--plan", plan, "--task", "5", "--state", "x",
              "--artifact", art / "dataset-manifest.json")
same = plan.read_text(encoding="utf-8") == before
fails += not case("[x] ahead of an unmet prerequisite is refused", "REFUSED", rc, out)
print(f"  {'PASS' if same else 'FAIL'} PLAN.md is byte-identical: the write was reverted")
fails += not same

print("\n=== a hard hold refuses before the write ===")
HELD = GOOD.replace(
    "3. [ ] **Register the dataset**",
    '3. [?] **Register the dataset** — asked: needs a versioned S3 URI and a role ARN')
ph = D / "held.md"; ph.write_text(HELD, encoding="utf-8")
ev = art / "leakage-audit.json"; ev.write_text("{}", encoding="utf-8")
rc, out = run("report.py", "--plan", ph, "--task", "4", "--state", "x", "--artifact", ev)
fails += not case("stage 3 holds hard: a downstream [x] is refused", "REFUSED", rc, out, 'holds = "hard"')
rc, out = run("report.py", "--plan", ph, "--task", "5", "--state", "S", "--reason", "not this time")
fails += not case("[S] is refused too, which plan-lint alone does not catch", "REFUSED", rc, out, 'holds = "hard"')
rc, out = run("report.py", "--plan", ph, "--task", "3", "--state", "x", "--artifact", ev)
fails += not case("the held task itself stays reportable: that is how the hold clears", "OK", rc, out)

print("\n=== an unapproved plan cannot record execution stages ===")
pu2 = D / "unappr2.md"; pu2.write_text(UNAPPROVED, encoding="utf-8")
rc, out = run("report.py", "--plan", pu2, "--task", "3", "--state", "x", "--artifact", ev)
fails += not case("calling report.py directly does not bypass the approval gate", "REFUSED", rc, out, "APPROVED")
rc, out = run("report.py", "--plan", pu2, "--task", "1", "--state", "x")
fails += not case("planning stages are exempt: they produce the plan to be approved", "OK", rc, out)

print("\n=== an artefact that records its own refusal cannot be [x] ===")
# Stage 3 must be settled first, or the ordering and prerequisite rules fire before the
# verdict check is ever reached -- which is what the first version of this case measured.
VERDICT = GOOD.replace("3. [ ] **Register the dataset**", "3. [x] **Register the dataset**")
gate_ok = art / "leakage-audit.json"
gate_ok.write_text(json.dumps({"status": "PASS", "refusedCount": 0}), encoding="utf-8")
pv = D / "verdict.md"; pv.write_text(VERDICT, encoding="utf-8")
rc, out = run("report.py", "--plan", pv, "--task", "4", "--state", "x", "--artifact", gate_ok)
fails += not case("a PASS verdict records normally", "OK", rc, out, "RECORDED")

pv.write_text(VERDICT, encoding="utf-8")
gate_ok.write_text(json.dumps({"status": "REFUSED", "refusedCount": 3}), encoding="utf-8")
rc, out = run("report.py", "--plan", pv, "--task", "4", "--state", "x", "--artifact", gate_ok)
fails += not case("a REFUSED verdict blocks [x]", "REFUSED", rc, out, "records its own refusal")

pv.write_text(VERDICT, encoding="utf-8")
gate_ok.write_text(json.dumps({"refusedCount": 0}), encoding="utf-8")
rc, out = run("report.py", "--plan", pv, "--task", "4", "--state", "x", "--artifact", gate_ok)
fails += not case("a missing verdict field is refused, not assumed to pass",
                  "REFUSED", rc, out, "does not contain it")

pv.write_text(VERDICT, encoding="utf-8")
gate_ok.write_text("not json at all", encoding="utf-8")
rc, out = run("report.py", "--plan", pv, "--task", "4", "--state", "x", "--artifact", gate_ok)
fails += not case("an unreadable artefact is not a pass", "REFUSED", rc, out, "could not be read")

print(f"\n{'all passed' if not fails else str(fails) + ' failed'}")
sys.exit(1 if fails else 0)
