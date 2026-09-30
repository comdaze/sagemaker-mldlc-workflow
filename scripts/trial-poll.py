#!/usr/bin/env python3
"""Follow a remote run through, against a fake `aws` on PATH, one condition at a time.

poll.py and the remote checks in report.py talk to SageMaker through the `aws` CLI, so this
suite puts a stand-in first on PATH: it answers from a JSON state file, advances a status
sequence on each describe, and logs every call so a case can assert what was asked (the
region, the profile). No test-only switch exists in the scripts themselves -- they find
`aws` exactly as a run does.

Each case checks for the INTENDED answer -- a needle in the output -- rather than merely for
some refusal, which is how the other suites in this directory are written.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "skills" / "ml-planning" / "scripts"
D = Path(os.environ.get("KIROCREW_SCRATCH", "/tmp")) / "poll"
shutil.rmtree(D, ignore_errors=True)
(D / "bin").mkdir(parents=True)
STATE, LOG = D / "aws-state.json", D / "aws-calls.jsonl"

FAKE = r'''#!__PY__
import json, os, sys
path = os.environ["FAKE_AWS_STATE"]
st = json.load(open(path))
argv = sys.argv[1:]
with open(os.environ["FAKE_AWS_LOG"], "a") as f:
    f.write(json.dumps(argv) + "\n")
def opt(name):
    return argv[argv.index(name) + 1] if name in argv else None
def die(msg):
    sys.stderr.write(msg + "\n"); sys.exit(254)
need = st.get("requireProfile")
if need and opt("--profile") != need:
    die("An error occurred (UnrecognizedClientException) when calling the %s operation: "
        "The security token included in the request is invalid." % argv[1])
svc, op = argv[0], argv[1]
def advance(key):
    e = st["resources"].get(key)
    if e is None:
        return None, None
    i = st.setdefault("reads", {}).get(key, 0)
    st["reads"][key] = i + 1
    json.dump(st, open(path, "w"))
    return e, min(i, len(e["statuses"]) - 1)
def body(e, i, field, arn=None):
    out = {field: e["statuses"][i], "CreationTime": "2026-09-30T10:00:00+08:00",
           "LastModifiedTime": "2026-09-30T10:20:00+08:00"}
    if arn:
        out["PipelineExecutionArn"] = arn
    if e.get("failure"):
        out["FailureReason"] = e["failure"]
    return out
if svc == "logs":
    prefix = opt("--log-stream-name-prefix") or ""
    for line in st.get("logs", {}).get(argv[2] + "|" + prefix, []):
        print(line)
    sys.exit(0)
if op == "describe-pipeline-execution":
    arn = opt("--pipeline-execution-arn")
    e, i = advance(arn)
    if e is None:
        die("An error occurred (ResourceNotFound) when calling the DescribePipelineExecution "
            "operation: Pipeline execution '%s' does not exist." % arn)
    print(json.dumps(body(e, i, "PipelineExecutionStatus", arn))); sys.exit(0)
if op == "list-pipeline-execution-steps":
    arn = opt("--pipeline-execution-arn")
    e = st["resources"][arn]
    i = max(0, min(st.get("reads", {}).get(arn, 1) - 1, len(e["statuses"]) - 1))
    steps = e.get("steps", [])
    if steps and isinstance(steps[0], list):
        steps = steps[min(i, len(steps) - 1)]
    print(json.dumps({"PipelineExecutionSteps": steps})); sys.exit(0)
JOBS = {"describe-training-job": ("--training-job-name", "TrainingJobStatus"),
        "describe-processing-job": ("--processing-job-name", "ProcessingJobStatus"),
        "describe-transform-job": ("--transform-job-name", "TransformJobStatus"),
        "describe-endpoint": ("--endpoint-name", "EndpointStatus")}
if op in JOBS:
    flag, field = JOBS[op]
    name = opt(flag)
    e, i = advance(op + ":" + name)
    if e is None:
        die("An error occurred (ValidationException) when calling the %s operation: "
            "Could not find requested job with name: %s" % (op, name))
    print(json.dumps(body(e, i, field))); sys.exit(0)
die("fake aws: unhandled %r" % argv)
'''
fake = D / "bin" / "aws"
fake.write_text(FAKE.replace("__PY__", sys.executable), encoding="utf-8")
fake.chmod(0o755)

ENV = {**os.environ, "PATH": f"{D / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
       "FAKE_AWS_STATE": str(STATE), "FAKE_AWS_LOG": str(LOG)}

ACCT = "123456789012"
P1 = f"arn:aws-cn:sagemaker:cn-north-1:{ACCT}:pipeline/demo/execution/run1"
P2 = f"arn:aws-cn:sagemaker:cn-north-1:{ACCT}:pipeline/demo/execution/run2"
GONE = f"arn:aws-cn:sagemaker:cn-north-1:{ACCT}:pipeline/demo/execution/never"
TJOB = "pipelines-run1-TrainModel-AbC123"
TJOB_ARN = f"arn:aws-cn:sagemaker:cn-north-1:{ACCT}:training-job/{TJOB}"
EP = f"arn:aws-cn:sagemaker:cn-north-1:{ACCT}:endpoint/demo-ep"
COMPUTE = "instanceType=ml.m5.large,instanceCount=1,useSpot=false"

STEPS_OK = [{"StepName": "Process", "StepStatus": "Succeeded"},
            {"StepName": "Train", "StepStatus": "Succeeded",
             "Metadata": {"TrainingJob": {"Arn": TJOB_ARN}}},
            {"StepName": "Evaluate", "StepStatus": "Succeeded"}]
STEPS_CRASH = [{"StepName": "Process", "StepStatus": "Succeeded"},
               {"StepName": "Train", "StepStatus": "Failed",
                "StartTime": "2026-09-30T10:05:00+08:00",
                "FailureReason": "AlgorithmError: ExecuteUserScriptError",
                "Metadata": {"TrainingJob": {"Arn": TJOB_ARN}}}]
STEPS_GATE = STEPS_OK + [{"StepName": "QualityGateFailed", "StepStatus": "Failed",
                          "Metadata": {"Fail": {"ErrorMessage": "mae 151.2 above bound 138.6"}}}]


def state(resources: dict, **extra) -> None:
    STATE.write_text(json.dumps({"resources": resources, **extra}), encoding="utf-8")
    LOG.write_text("", encoding="utf-8")


def calls() -> list[list[str]]:
    return [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]


def pipeline(statuses, steps=None, failure=None):
    return {"statuses": statuses, "steps": steps or [], **({"failure": failure} if failure else {})}


def run(script, *a):
    (D / "PLAN.state.json").unlink(missing_ok=True)
    p = subprocess.run([sys.executable, str(S / script), *map(str, a)],
                       capture_output=True, text=True, env=ENV)
    return p.returncode, (p.stdout + p.stderr).strip()


fails = 0


def case(label, want_rc, rc, out, *needles):
    global fails
    ok = rc == want_rc and all(n in out for n in needles)
    print(f"  {'PASS' if ok else 'FAIL'} {label:<64} rc={rc}")
    if not ok:
        missing = [n for n in needles if n not in out]
        print(f"      wanted rc={want_rc}" + (f", missing {missing}" if missing else ""))
        print("      " + out.replace("\n", "\n      ")[:900])
    fails += not ok
    return ok


def check(label, ok):
    global fails
    print(f"  {'PASS' if ok else 'FAIL'} {label}")
    fails += not ok


HEAD = f"""# Plan

PARTITION: aws-cn
AWS_PROFILE: cn-profile
SDK: 3.22.0
PRESET: batch-serving
APPROVED: "go with batch-serving" @ 2026-09-18T13:05:00+08:00
COMPUTE: ml.m5.large x1, spot=false, maxRuntimeMin=60, authorisedBy=user @ 2026-09-18T21:40:00+08:00
LAST_DONE: {{last}} @ 2026-09-18T10:00:00+08:00

"""
NAMES = {1: ("Frame", "ml-planning"), 2: ("Environment", "ml-planning"),
         3: ("Register", "data-pipeline"), 4: ("Leakage", "leakage-guard"),
         5: ("Process", "data-pipeline"), 6: ("Baseline", "train-and-tune"),
         7: ("Train", "train-and-tune"), 8: ("Tune", "train-and-tune"),
         9: ("Evaluate", "evaluate-and-gate"), 10: ("Gate", "evaluate-and-gate"),
         11: ("Register model", "release-and-serve"), 12: ("Release", "release-and-serve"),
         13: ("Batch", "release-and-serve")}


def make(last: int, marks: dict[int, str], extra: dict[int, str] | None = None) -> str:
    """A 13-task batch-serving plan: tasks up to `last` are [x], the rest [ ] unless marked."""
    extra = extra or {}
    lines = []
    for n, (name, owner) in NAMES.items():
        m = marks.get(n, "[x]" if n <= last else "[ ]")
        note = extra.get(n, "")
        if n == 3 and m == "[x]":
            note = ("supplied: bucket=b, role=r " + note).strip()
        lines.append(f"{n}. {m} **{name}** {note} _(Stage: {n} | Skill: {owner})_".replace("  ", " "))
    return HEAD.replace("{last}", str(last)) + "\n".join(lines) + "\n"


# Task 9 (stage 9, Evaluation) is the workhorse: pipeline-mode, billable, and it declares an
# artefact but no gate script -- so an [x] exercises the remote check without also needing a
# checker's inputs to be valid.
AT9 = make(7, {8: "[S]", 9: "[>]"}, {8: "skipped: not this run",
                                    9: f"execution: {P1} compute: {COMPUTE}"})
plan = D / "PLAN.md"
art = D / "artifacts"
art.mkdir()
evaluation = art / "evaluation-report.json"
evaluation.write_text(json.dumps({"metric": "mae", "value": 131.0}), encoding="utf-8")

print("=== the fixture plans are lint-clean before anything is tested ===")
plan.write_text(AT9, encoding="utf-8")
rc, out = run("plan-lint.py", plan)
case("a [>] task on a pipeline stage", 0, rc, out, "OK")

print("\n=== poll.py waits, and ends on a verdict ===")
state({P1: pipeline(["Executing", "Executing", "Succeeded"], STEPS_OK)})
rc, out = run("poll.py", "--plan", plan, "--task", 9, "--interval", 0, "--wait-min", 1)
case("it keeps reading until the run ends, then says SUCCEEDED", 0, rc, out,
     "VERDICT  SUCCEEDED", "--state x", "evaluation-report.json", f'--compute "{COMPUTE}"')
reads = [c for c in calls() if c[:2] == ["sagemaker", "describe-pipeline-execution"]]
check("three describes: Executing, Executing, Succeeded", len(reads) == 3)
check("the region comes from the ARN", all(c[c.index("--region") + 1] == "cn-north-1"
                                            for c in reads))
check("the profile comes from PLAN.md's AWS_PROFILE line",
      all("--profile" in c and c[c.index("--profile") + 1] == "cn-profile" for c in reads))

state({P1: pipeline(["Executing"], STEPS_OK[:1])})
rc, out = run("poll.py", "--plan", plan, "--task", 9, "--wait-min", 0)
case("a run still going ends the segment RUNNING, exit 3", 3, rc, out,
     "VERDICT  RUNNING", "again now, in this turn", "console link")

state({P1: pipeline(["Failed"], STEPS_CRASH, "Step failure: One or multiple steps failed.")},
      logs={f"/aws/sagemaker/TrainingJobs|{TJOB}/": [
          "Traceback (most recent call last):", "KeyError: 'target'"]})
rc, out = run("poll.py", "--plan", plan, "--task", 9, "--wait-min", 0)
case("a crash names the step, the reason and the container's own words", 1, rc, out,
     "VERDICT  FAILED", "Train [Failed]", "ExecuteUserScriptError", "KeyError: 'target'",
     "--state R")
tails = [c for c in calls() if c[:2] == ["logs", "tail"]]
check("the log tail is read from the failed job's own stream",
      len(tails) == 1 and tails[0][2] == "/aws/sagemaker/TrainingJobs"
      and tails[0][tails[0].index("--log-stream-name-prefix") + 1] == f"{TJOB}/")

state({P1: pipeline(["Failed"], STEPS_GATE, "Step failure: One or multiple steps failed.")})
rc, out = run("poll.py", "--plan", plan, "--task", 9, "--wait-min", 0)
case("a FailStep reads as a gate refusing, not a crash", 1, rc, out,
     "FailStep", "mae 151.2 above bound 138.6", "--step")

state({P1: pipeline(["Stopped"], STEPS_OK[:1])})
rc, out = run("poll.py", "--plan", plan, "--task", 9, "--wait-min", 0)
case("a stopped run is STOPPED, and resubmitting is the user's call", 1, rc, out,
     "VERDICT  STOPPED", "a decision you did not make")

state({P1: pipeline(["Paused"])})
rc, out = run("poll.py", "--plan", plan, "--task", 9, "--wait-min", 0)
case("a status nobody has a reading for is refused, never taken as done", 2, rc, out,
     "no reading")

state({P1: pipeline(["Succeeded"], STEPS_OK)}, requireProfile="cn-profile")
nop = D / "noprofile.md"
nop.write_text(AT9.replace("AWS_PROFILE: cn-profile\n", ""), encoding="utf-8")
rc, out = run("poll.py", "--plan", nop, "--task", 9, "--wait-min", 0)
case("the wrong credentials are named as such, with the fix", 2, rc, out,
     "cannot read this execution", "AWS_PROFILE")

state({})
rc, out = run("poll.py", "--plan", plan, "--execution",
              P1.replace("arn:aws-cn:", "arn:aws:"), "--wait-min", 0)
case("an ARN in another partition than the plan's is refused", 2, rc, out, "partition")
rc, out = run("poll.py", "--plan", plan, "--execution",
              f"arn:aws-cn:sagemaker:cn-north-1:{ACCT}:pipeline/demo", "--wait-min", 0)
case("a pipeline is not a run of it", 2, rc, out, "names the pipeline")
bare = D / "bare.md"
bare.write_text(AT9.replace(f"execution: {P1}", "execution: run1"), encoding="utf-8")
rc, out = run("poll.py", "--plan", bare, "--task", 9, "--wait-min", 0)
case("a bare id cannot be followed", 2, rc, out, "records no `execution: <arn>`")

state({"describe-endpoint:demo-ep": {"statuses": ["Creating", "InService"]}})
rc, out = run("poll.py", "--execution", EP, "--interval", 0, "--wait-min", 1)
case("an endpoint is followed to InService, then sent to the smoke test", 0, rc, out,
     "VERDICT  SUCCEEDED", "smoke request")

SHARED = make(4, {5: "[>]", 6: "[>]"}, {5: f"execution: {P1} compute: {COMPUTE}",
                                         6: f"execution: {P1} compute: {COMPUTE}"})
shared = D / "shared.md"
shared.write_text(SHARED, encoding="utf-8")
state({P1: pipeline(["Succeeded"], STEPS_OK)})
rc, out = run("poll.py", "--plan", shared, "--task", 5, "--wait-min", 0)
case("stages sharing one execution are all named for reporting", 0, rc, out,
     "share this execution", "--task 5 --state x", "--task 6 --state x")

print("\n=== one Pipeline submission may put several stages at [>] ===")
# This had been documented since [>] existed and was refused by the prerequisite check:
# stage 6 [>] beside stage 5 [>] read as an unsettled prerequisite.
rc, out = run("plan-lint.py", shared)
case("stages 5 and 6 [>] on the SAME execution are lint-clean", 0, rc, out, "OK")
split = D / "split.md"
split.write_text(SHARED.replace(f"6. [>] **Baseline** execution: {P1}",
                                f"6. [>] **Baseline** execution: {P2}"), encoding="utf-8")
rc, out = run("plan-lint.py", split)
case("on DIFFERENT executions the prerequisite rule still holds", 1, rc, out, "prerequisite")

print("\n=== report.py: [>] must name a run that exists ===")
TO5 = make(4, {})
plan.write_text(TO5, encoding="utf-8")
state({P1: pipeline(["Executing"])})
rc, out = run("report.py", "--plan", plan, "--task", 5, "--state", ">",
              "--execution", "run1", "--compute", COMPUTE)
case("a bare id is refused", 2, rc, out, "not a SageMaker ARN")
rc, out = run("report.py", "--plan", plan, "--task", 5, "--state", ">",
              "--execution", GONE, "--compute", COMPUTE)
case("an ARN SageMaker cannot find is refused", 2, rc, out, "does not exist")
rc, out = run("report.py", "--plan", plan, "--task", 5, "--state", ">",
              "--execution", P1, "--compute", COMPUTE)
case("a real one is recorded", 0, rc, out, "RECORDED", f"execution: {P1}")
rc, out = run("report.py", "--plan", plan, "--task", 6, "--state", ">",
              "--execution", P1, "--compute", COMPUTE)
case("and a second stage of the same submission can join it", 0, rc, out, "RECORDED")
rc, out = run("report.py", "--plan", plan, "--task", 6, "--state", ">",
              "--execution", P1, "--compute", COMPUTE)
case("re-reporting the same execution records nothing new", 2, rc, out, "already [>]")

print("\n=== report.py: leaving [>] reads how the run ended ===")
plan.write_text(AT9, encoding="utf-8")
before = AT9
state({P1: pipeline(["Executing"], STEPS_OK[:1])})
rc, out = run("report.py", "--plan", plan, "--task", 9, "--state", "x",
              "--artifact", evaluation, "--compute", COMPUTE)
case("[x] while the run is still going is refused", 2, rc, out, "still Executing",
     "poll.py")
check("and PLAN.md is untouched", plan.read_text(encoding="utf-8") == before)
rc, out = run("report.py", "--plan", plan, "--task", 9, "--state", "R")
case("so is [R]: a running, billing run cannot be dropped", 2, rc, out, "still Executing")

state({P1: pipeline(["Failed"], STEPS_CRASH, "Step failure: One or multiple steps failed.")})
rc, out = run("report.py", "--plan", plan, "--task", 9, "--state", "x",
              "--artifact", evaluation, "--compute", COMPUTE)
case("[x] on a run that failed is refused, pointing at [R]", 2, rc, out, "ended Failed",
     "Report [R]")
rc, out = run("report.py", "--plan", plan, "--task", 9, "--state", "R")
case("[R] on a run that failed is recorded with its status", 0, rc, out, "remote: Failed")
led = json.loads((D / "PLAN.state.json").read_text(encoding="utf-8"))
last = (led.get("reports") or [{}])[-1].get("remote") or {}
check("and the ledger keeps the failure reason the plan line does not",
      last.get("failure") == "Step failure: One or multiple steps failed.")

plan.write_text(AT9, encoding="utf-8")
state({P1: pipeline(["Failed"], STEPS_GATE, "Step failure: One or multiple steps failed.")})
rc, out = run("report.py", "--plan", plan, "--task", 9, "--state", "x", "--step", "Evaluate",
              "--artifact", evaluation, "--compute", COMPUTE)
case("a FailStep run: [x] with this stage's own succeeded step", 0, rc, out,
     "steps Evaluate Succeeded")
plan.write_text(AT9, encoding="utf-8")
rc, out = run("report.py", "--plan", plan, "--task", 9, "--state", "x",
              "--step", "QualityGateFailed", "--artifact", evaluation, "--compute", COMPUTE)
case("but naming the failed step is refused", 2, rc, out, "not Succeeded")
rc, out = run("report.py", "--plan", plan, "--task", 9, "--state", "x", "--step", "Nope",
              "--artifact", evaluation, "--compute", COMPUTE)
case("and so is naming a step the run does not have", 2, rc, out, "not in this execution")

plan.write_text(AT9, encoding="utf-8")
state({P1: pipeline(["Succeeded"], STEPS_OK)})
rc, out = run("report.py", "--plan", plan, "--task", 9, "--state", "x",
              "--artifact", evaluation, "--compute", COMPUTE)
case("[x] on a run that succeeded is recorded with its status", 0, rc, out,
     "remote: Succeeded")
rc, out = run("report.py", "--plan", plan, "--task", 10, "--state", "x", "--step", "Evaluate")
case("--step on a task that is not [>] is refused", 2, rc, out,
     "only means something for a task leaving [>]")

print("\n=== next.py: a submission is followed, in the same turn ===")
plan.write_text(AT9, encoding="utf-8")
rc, out = run("next.py", plan)
case("a [>] task gets a poll directive with the command and the ARN", 0, rc, out,
     "poll", "poll.py", "--task 9", P1)
case("and it says to stay in the turn", 0, rc, out, "STAY IN THIS TURN", "console link")
plan.write_text(TO5, encoding="utf-8")
rc, out = run("next.py", plan)
case("a pipeline stage's execute directive says what follows the submit", 0, rc, out,
     "execute", "report [>]", "next.py again before ending that turn")

print(f"\n{'all passed' if not fails else str(fails) + ' failed'}")
sys.exit(1 if fails else 0)
