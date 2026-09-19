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

# Stage 3's gate is contract-check.py, so `{}` is no longer enough to record that stage --
# which is the point of wiring the checkers in. contract-check names six missing identity
# fields for an empty object, and a digest that is not 64 hex characters is a seventh.
MANIFEST = json.dumps({
    "uri": "s3://bucket/data.csv",
    "versionId": "v-abc123",
    "etag": '"abc123"',
    "digest": "sha256:" + "ab" * 32,
    "sampleCount": 34944,
    "readBackVerified": True,
    "completeness": {"asserted": True, "expectedPerDay": 96},
    "scope": {"included": "the whole source file", "excluded": [],
              "scopeChosenBy": "user"},
})

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
(art / "dataset-manifest.json").write_text(MANIFEST, encoding="utf-8")
rc, out = run("report.py", "--plan", plan, "--task", "3", "--state", "x",
              "--supplied", "bucket=b, role=r, prefix=p/", "--artifact", art / "dataset-manifest.json")
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
rc, out = run("report.py", "--plan", ph, "--task", "3", "--state", "x", "--supplied", "bucket=b, role=r, prefix=p/", "--artifact", ev)
fails += not case("the held task itself stays reportable: that is how the hold clears", "OK", rc, out)

print("\n=== an unapproved plan cannot record execution stages ===")
pu2 = D / "unappr2.md"; pu2.write_text(UNAPPROVED, encoding="utf-8")
rc, out = run("report.py", "--plan", pu2, "--task", "3", "--state", "x", "--supplied", "bucket=b, role=r, prefix=p/", "--artifact", ev)
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

print("\n=== a stage whose gate is a separate program must run it ===")
# Stage 6's gate is training-check.py. Feed it a baseline report naming a strongest baseline
# that is not in its own computed set -- the field the checker actually reads. Three earlier
# attempts at this case broke `baselines` and `strongestBaseline`, which that artefact also
# carries as aliases and the checker ignores, so the gate passed and proved nothing.
GATED = GOOD.replace("PRESET: data-prep-only", "PRESET: batch-serving")
GATED = GATED.replace("LAST_DONE:", "COMPUTE: ml.m5.large x1, spot=false, maxRuntimeMin=60, authorisedBy=user @ 2026-09-18T21:40:00+08:00\nLAST_DONE:")
for n, s in ((4, "Leakage"), (5, "Processing")):
    GATED = GATED.replace(f"{n}. [ ] **", f"{n}. [x] **")
GATED = GATED.replace("3. [ ] **Register the dataset**", "3. [x] **Register the dataset**")
GATED += "".join(
    f"{n}. [ ] **T{n}** _(Stage: {n} | Skill: {o})_\n" for n, o in (
        (6, "train-and-tune"), (7, "train-and-tune"), (8, "train-and-tune"),
        (9, "evaluate-and-gate"), (10, "evaluate-and-gate"),
        (11, "release-and-serve"), (12, "release-and-serve"), (13, "release-and-serve")))

base = art / "baseline-report.json"
train = art / "training-report.json"
# The bound must be derivable from the strongest baseline, which is check_bound's rule --
# a report without it is refused for that reason and would have masked what this case tests.
# The bound is derived, so derive it here too rather than asserting a number that must happen to
# equal the arithmetic check_bound performs.
STRONGEST, MARGIN_PCT = 140.0, 1.0
good_base = {"metric": "mae",
             "computed": [{"name": "mean", "score": 180.0},
                          {"name": "yesterday", "score": STRONGEST}],
             "strongest": "yesterday",
             "quality": {"metric": "mae", "bound": STRONGEST * (1 - MARGIN_PCT / 100),
                         "marginPct": MARGIN_PCT,
                         "derivedFrom": "yesterday",
                         "contract": "contracts/quality-gate-contract.json",
                         "marginChosenBy": "user", "metricChosenBy": "user"}}
pg = D / "gated.md"

pg.write_text(GATED, encoding="utf-8")
base.write_text(json.dumps(good_base), encoding="utf-8")
rc, out = run("report.py", "--plan", pg, "--task", "6", "--state", "x", "--artifact", base,
           "--compute", "instanceType=ml.m5.large,instanceCount=1,useSpot=false")
fails += not case("the gate runs and its name is recorded", "OK", rc, out, "gate passed")

pg.write_text(GATED, encoding="utf-8")
base.write_text(json.dumps({**good_base, "strongest": "not-computed"}), encoding="utf-8")
rc, out = run("report.py", "--plan", pg, "--task", "6", "--state", "x", "--artifact", base,
           "--compute", "instanceType=ml.m5.large,instanceCount=1,useSpot=false")
fails += not case("the checker's own refusal reaches the caller", "REFUSED", rc, out,
                  "not among the computed set")

# Stage 7's gate needs the baseline AND the training report. Supplying only the training
# report satisfies `produces` -- so the produces check passes and the gate is what refuses,
# which is the ordering this case has to exercise.
pg.write_text(GATED.replace("6. [ ] **T6**", "6. [x] **T6**"), encoding="utf-8")
train.write_text(json.dumps({"status": "Completed"}), encoding="utf-8")
base.unlink(missing_ok=True)
rc, out = run("report.py", "--plan", pg, "--task", "7", "--state", "x", "--artifact", train,
           "--compute", "instanceType=ml.m5.large,instanceCount=1,useSpot=false")
fails += not case("a gate that cannot run has not passed", "REFUSED", rc, out,
                  "cannot run has not passed")
train.unlink(missing_ok=True)

print("\n=== a hard hold completed must record what the user supplied ===")
HELD_OK = GOOD.replace("PRESET: data-prep-only", "PRESET: data-prep-only")
ph2 = D / "supplied.md"
mf = art / "dataset-manifest.json"
mf.write_text(MANIFEST, encoding="utf-8")
ph2.write_text(GOOD, encoding="utf-8")
rc, out = run("report.py", "--plan", ph2, "--task", "3", "--state", "x", "--artifact", mf)
fails += not case("[x] on a hard-hold stage without --supplied is refused",
                  "REFUSED", rc, out, "came from the user")
ph2.write_text(GOOD, encoding="utf-8")
rc, out = run("report.py", "--plan", ph2, "--task", "3", "--state", "x", "--artifact", mf,
              "--supplied", "bucket=b, role=r, prefix=p/")
fails += not case("naming the supplied values records them in the plan", "OK", rc, out,
                  "supplied:")

print("\n=== a substantive edit trips the digest, not only a marker edit ===")
ph3 = D / "substance.md"
ph3.write_text(GOOD.replace("**Register the dataset**",
                            "**Register the dataset at s3://generic/**"), encoding="utf-8")
run("next.py", ph3)
before_txt = ph3.read_text(encoding="utf-8")
ph3.write_text(before_txt.replace("s3://generic/", "s3://restricted/"), encoding="utf-8")
rc, out = run("next.py", ph3, "--no-record")
fails += not case("changing a prefix in the plan text is caught", "REFUSED", rc, out,
                  "some route other than report.py")

print("\n=== a billable stage is not dispatched until its cost is authorised ===")
BILL = GOOD.replace("3. [ ] **Register the dataset**",
                    "3. [x] **Register the dataset** supplied: bucket=b, role=r, prefix=p/")
BILL = BILL.replace("4. [ ] **Leakage guard**", "4. [x] **Leakage guard**")
BILL = BILL.replace("LAST_DONE: 2 @", "LAST_DONE: 4 @")
pb = D / "billable.md"
(D / "PLAN.state.json").unlink(missing_ok=True)
pb.write_text(BILL, encoding="utf-8")
rc, out = run("next.py", pb)
fails += not case("stage 5 spends money, so the directive asks for authorisation",
                  "OK", rc, out, "authorise-compute")
no_exec = "DIRECTIVE 1  execute" not in out
print(f"  {'PASS' if no_exec else 'FAIL'} and it is not dispatched to execute")
fails += not no_exec

pb.write_text(BILL, encoding="utf-8")
rc, out = run("report.py", "--plan", pb, "--task", "5", "--state", ">",
              "--execution", "arn:aws-cn:sagemaker:…:pipeline-execution/abc")
fails += not case("reporting it directly does not bypass the compute gate",
                  "REFUSED", rc, out, "no authorised COMPUTE line")

AUTH = BILL.replace("LAST_DONE: 4 @",
                    "COMPUTE: ml.m5.large x1, spot=false, maxRuntimeMin=60, "
                    "authorisedBy=user @ 2026-09-18T21:40:00+08:00\nLAST_DONE: 4 @")
pb.write_text(AUTH, encoding="utf-8")
(D / "PLAN.state.json").unlink(missing_ok=True)
rc, out = run("next.py", pb)
fails += not case("once authorised, the billable stage dispatches", "OK", rc, out, "execute")

pb.write_text(AUTH, encoding="utf-8")
rc, out = run("report.py", "--plan", pb, "--task", "5", "--state", ">",
              "--execution", "arn:aws-cn:sagemaker:…:pipeline-execution/abc")
fails += not case("but what it ran on must still be recorded", "REFUSED", rc, out, "--compute")

print("\n=== the quality gate is arithmetic, not a job ===")
# stage 10 was once declared pipeline-mode and billable, which made the loop demand a compute
# authorisation before the gate could be reported. A run asked its user to authorise an instance
# for comparing two numbers; the user said none was needed and the run was right.
GATE10 = GATED.replace("10. [ ] **T10**", "10. [ ] **Gate**")
for n in (6, 7, 8, 9):
    GATE10 = GATE10.replace(f"{n}. [ ] **T{n}**", f"{n}. [x] **T{n}**")
GATE10 = GATE10.replace("LAST_DONE: 2 @", "LAST_DONE: 9 @")
# no COMPUTE line at all
GATE10 = "\n".join(l for l in GATE10.splitlines() if not l.startswith("COMPUTE:")) + "\n"
pg10 = D / "gate10.md"
qgr = art / "quality-gate-report.json"
qgr.write_text(json.dumps({"registrationAllowed": True, "status": "PASS"}), encoding="utf-8")

pg10.write_text(GATE10, encoding="utf-8")
rc, out = run("report.py", "--plan", pg10, "--task", "10", "--state", "x", "--artifact", qgr)
fails += not case("stage 10 records without any compute authorisation", "OK", rc, out, "RECORDED")

pg10.write_text(GATE10, encoding="utf-8")
rc, out = run("report.py", "--plan", pg10, "--task", "10", "--state", ">",
              "--execution", "arn:x")
fails += not case("and [>] is refused: nothing was submitted", "REFUSED", rc, out,
                  "no remote execution to await")

print("\n=== decisions are named in the directive, before the work ===")
# The *ChosenBy refusals all fire when the report arrives -- after the jobs have run. A user asked
# for the two tuning methods to be PUT to them instead of the run being caught not having asked.
pd = D / "decides.md"
DEC = GATED.replace("LAST_DONE: 2 @", "LAST_DONE: 7 @")
for n in (4, 5, 6, 7):
    DEC = DEC.replace(f"{n}. [ ] **T{n}**", f"{n}. [x] **T{n}**")
DEC = DEC.replace("3. [x] **Register the dataset**",
                  "3. [x] **Register the dataset** supplied: bucket=b, role=r")
pd.write_text(DEC, encoding="utf-8")
rc, out = run("next.py", pd)
fails += not case("the stage 8 directive names the method choice", "OK", rc, out,
                  "fixed-candidates")
for needle, label in (("DECIDE FIRST", "it heads them as the user's"),
                      ("END YOUR TURN", "and says to stop and wait"),
                      ("strategyChosenBy", "including the strategy"),
                      ("computeChosenBy", "and the compute behind the search")):
    ok = needle in out
    print(f"  {'PASS' if ok else 'FAIL'} {label}")
    fails += not ok

print(f"\n{'all passed' if not fails else str(fails) + ' failed'}")
sys.exit(1 if fails else 0)
