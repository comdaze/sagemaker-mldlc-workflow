#!/usr/bin/env python3
"""Wait for a submitted SageMaker execution and say how it ended, so a run does not stop at
the submission.

`[>]` records that work was handed to a remote executor, and `next.py` puts a `poll`
directive ahead of any new work while one is open. That much held. What did not: the
directive said "check the execution named in the task", and nothing said how, how often,
for how long, or what to do with the answer. Whether a run followed its own submission
through was the agent's call again -- and a user of this power described the predictable
result: a stage ends with a job submitted and a pointer to the console, and the person who
asked for the result has to go and find it.

This makes the check a command with an answer. It reads the execution the task names,
waits in bounded segments, and ends on one of four verdicts:

    SUCCEEDED  exit 0   the run finished; report it
    FAILED     exit 1   it ended badly; the failing step, its reason and its log tail follow
    STOPPED    exit 1   somebody or something stopped it
    RUNNING    exit 3   still going when this segment's budget ran out; run it again

Exit 2 is a refusal: no ARN, a partition that disagrees with the plan, credentials that
cannot see the execution, a status this script does not know how to read.

WHY SEGMENTS. A single agent command has a ceiling -- 30 minutes in Kiro, two by default --
and a training job does not care. So each call waits at most `--wait-min` (default 20) and
returns RUNNING, and the caller runs it again. The loop belongs to the caller; the output
says so in terms that are hard to misread.

WHAT IT DOES NOT DO. It writes nothing: not PLAN.md, not the ledger. Against AWS it is
read-only -- Describe* and List* calls, plus `logs tail` when something failed. `report.py`
stays the only writer of state, and it calls into this module to read the remote status
itself rather than trusting a claim about it.

Standard library plus the `aws` CLI on PATH, the same one that submitted the run. boto3
would be one more thing that has to import in whichever Python runs this, and no script in
this skill has needed a package yet.

    python3 poll.py --task 5                     # the execution task 5 names in PLAN.md
    python3 poll.py --execution <arn> --wait-min 0   # one look, no waiting
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


class Refusal(Exception):
    """A condition under which no verdict can be given."""


class AwsError(Exception):
    """The CLI answered with an error. `kind` says whether waiting could ever fix it."""

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind          # not-found | credentials | cli | transient


# ---------------------------------------------------------------- what an ARN names

ARN_RE = re.compile(
    r"^arn:(?P<partition>aws|aws-cn|aws-us-gov):sagemaker:(?P<region>[a-z0-9-]+):"
    r"(?P<account>\d{12}):(?P<resource>\S+)$"
)
PIPELINE_EXEC_RE = re.compile(r"^pipeline/(?P<pipeline>[^/]+)/execution/(?P<id>[^/]+)$")
JOB_RE = re.compile(
    r"^(?P<kind>training-job|processing-job|transform-job|hyper-parameter-tuning-job|endpoint)"
    r"/(?P<name>[^/]+)$"
)

# The CLI call that reads each kind, and the response field holding its status.
DESCRIBE = {
    "pipeline-execution": ("describe-pipeline-execution", "--pipeline-execution-arn",
                           "PipelineExecutionStatus"),
    "training-job": ("describe-training-job", "--training-job-name", "TrainingJobStatus"),
    "processing-job": ("describe-processing-job", "--processing-job-name",
                       "ProcessingJobStatus"),
    "transform-job": ("describe-transform-job", "--transform-job-name", "TransformJobStatus"),
    "hyper-parameter-tuning-job": ("describe-hyper-parameter-tuning-job",
                                   "--hyper-parameter-tuning-job-name",
                                   "HyperParameterTuningJobStatus"),
    "endpoint": ("describe-endpoint", "--endpoint-name", "EndpointStatus"),
}

# Every status the service model enumerates, mapped to a verdict. Taken from the CLI's own
# service-2.json rather than from memory. A status missing from this table is UNKNOWN, and
# UNKNOWN is never treated as terminal or as success: a new status the table has not met
# should stop a report, not slip through one.
_JOB = {"Completed": "SUCCEEDED", "Failed": "FAILED", "Stopped": "STOPPED",
        "InProgress": "RUNNING", "Stopping": "RUNNING", "Deleting": "STOPPED"}
VERDICT = {
    "pipeline-execution": {"Succeeded": "SUCCEEDED", "Failed": "FAILED", "Stopped": "STOPPED",
                           "Executing": "RUNNING", "Stopping": "RUNNING"},
    "training-job": _JOB,
    "processing-job": _JOB,
    "transform-job": _JOB,
    "hyper-parameter-tuning-job": {**_JOB, "DeleteFailed": "FAILED"},
    # InService is success only in the narrow sense that the endpoint says it can take
    # requests. release-and-serve still wants a smoke request before anyone believes it.
    "endpoint": {"InService": "SUCCEEDED", "Failed": "FAILED",
                 "UpdateRollbackFailed": "FAILED", "OutOfService": "FAILED",
                 "Deleting": "STOPPED", "Creating": "RUNNING", "Updating": "RUNNING",
                 "SystemUpdating": "RUNNING", "RollingBack": "RUNNING"},
}
STEP_VERDICT = {"Succeeded": "SUCCEEDED", "Failed": "FAILED", "Stopped": "STOPPED",
                "Starting": "RUNNING", "Executing": "RUNNING", "Stopping": "RUNNING"}
TERMINAL = {"SUCCEEDED", "FAILED", "STOPPED"}
EXIT = {"SUCCEEDED": 0, "FAILED": 1, "STOPPED": 1, "RUNNING": 3}

# Where each job kind writes its container logs. The stream prefix is the job name.
LOG_GROUP = {"training-job": "/aws/sagemaker/TrainingJobs",
             "processing-job": "/aws/sagemaker/ProcessingJobs",
             "transform-job": "/aws/sagemaker/TransformJobs"}
STEP_JOB_KEYS = (("TrainingJob", "training-job"), ("ProcessingJob", "processing-job"),
                 ("TransformJob", "transform-job"), ("TuningJob", "hyper-parameter-tuning-job"))


def parse_arn(arn: str) -> dict:
    """Split an ARN into what the CLI needs to read it. Refuse anything it cannot read."""
    arn = (arn or "").strip()
    m = ARN_RE.match(arn)
    if not m:
        raise Refusal(
            f"{arn!r} is not a SageMaker ARN. The execution has to be named by the ARN the "
            "API returned -- arn:<partition>:sagemaker:<region>:<account>:<resource> -- "
            "because that is what carries the region and account to check it in. A bare id "
            "cannot be checked by anyone, so it cannot stand for a run."
        )
    t = {"arn": arn, "partition": m["partition"], "region": m["region"],
         "account": m["account"]}
    res = m["resource"]
    pm = PIPELINE_EXEC_RE.match(res)
    if pm:
        return {**t, "kind": "pipeline-execution", "name": pm["pipeline"], "id": pm["id"]}
    jm = JOB_RE.match(res)
    if jm:
        return {**t, "kind": jm["kind"], "name": jm["name"]}
    if res.startswith("pipeline/"):
        raise Refusal(
            f"{arn} names the pipeline, not a run of it. A pipeline has no outcome; an "
            "execution does. Use the PipelineExecutionArn that start-pipeline-execution "
            "returned: .../pipeline/<name>/execution/<id>."
        )
    raise Refusal(
        f"{arn} names a {res.split('/')[0]!r}, which this cannot follow. It reads pipeline "
        "executions and training, processing, transform, tuning jobs and endpoints."
    )


COMPUTE_FIELD_RE = re.compile(r"compute:\s*(?P<val>\S+)")


def resolve_profile(explicit: str | None, header: dict) -> str | None:
    """--profile, then the plan's `AWS_PROFILE:` line, then whatever the CLI would pick.

    The plan line exists because a resumed session reads PARTITION and nothing else about
    where the run lives. On a machine holding several accounts the CLI's default credentials
    are often another partition entirely, and a describe with those fails in a way that looks
    exactly like the run is missing.
    """
    if explicit:
        return explicit
    val = (header.get("AWS_PROFILE") or (0, ""))[1].strip()
    return val or None


def check_partition(target: dict, header: dict) -> None:
    want = (header.get("PARTITION") or (0, ""))[1].strip()
    if want and want != target["partition"]:
        raise Refusal(
            f"the execution's ARN is in partition {target['partition']}, and PLAN.md says "
            f"PARTITION: {want}. One of them is wrong. Settle which before reading it: "
            "credentials from the other partition fail in a way that reads like a missing run."
        )


# ---------------------------------------------------------------- talking to AWS

_CREDENTIALS = ("ExpiredToken", "security token", "UnrecognizedClient", "InvalidClientTokenId",
                "Unable to locate credentials", "could not be found", "AccessDenied",
                "not authorized", "SignatureDoesNotMatch")
_NOT_FOUND = ("ResourceNotFound", "does not exist", "Could not find", "not found")
_TRANSIENT = ("Throttling", "ThrottlingException", "Rate exceeded", "ServiceUnavailable",
              "InternalFailure", "Could not connect", "Connection was closed",
              "Read timeout", "timed out")


def _cli(args: list[str], region: str, profile: str | None, timeout: int = 90) -> str:
    exe = shutil.which("aws")
    if exe is None:
        raise AwsError("the aws CLI is not on PATH, so nothing can be read from SageMaker. "
                       "It is the same CLI that submits the run; install or expose it.", "cli")
    cmd = [exe, *args, "--region", region]
    if profile:
        cmd += ["--profile", profile]
    env = {**os.environ, "AWS_PAGER": ""}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise AwsError(f"`{' '.join(args[:2])}` did not answer within {timeout}s", "transient")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
        if any(s in err for s in _CREDENTIALS):
            kind = "credentials"
        elif any(s in err for s in _NOT_FOUND):
            kind = "not-found"
        elif any(s in err for s in _TRANSIENT):
            kind = "transient"
        else:
            kind = "cli"
        raise AwsError(err, kind)
    return proc.stdout


def _json(args: list[str], region: str, profile: str | None) -> dict:
    out = _cli([*args, "--output", "json"], region, profile)
    try:
        return json.loads(out or "{}")
    except json.JSONDecodeError as exc:
        raise AwsError(f"`{' '.join(args[:2])}` returned something that is not JSON ({exc})",
                       "cli")


def _job_name(arn: str) -> str:
    return arn.rsplit("/", 1)[-1]


def snapshot(target: dict, profile: str | None) -> dict:
    """One read of the execution: its status, its verdict and, for a pipeline, every step."""
    op, flag, field = DESCRIBE[target["kind"]]
    ident = target["arn"] if target["kind"] == "pipeline-execution" else target["name"]
    doc = _json(["sagemaker", op, flag, ident], target["region"], profile)
    status = doc.get(field)
    snap = {
        "arn": target["arn"], "kind": target["kind"], "region": target["region"],
        "status": status, "verdict": VERDICT[target["kind"]].get(status, "UNKNOWN"),
        "failure": doc.get("FailureReason"), "exitMessage": doc.get("ExitMessage"),
        "created": doc.get("CreationTime"), "modified": doc.get("LastModifiedTime"),
        "steps": [],
    }
    if target["kind"] == "pipeline-execution":
        listing = _json(["sagemaker", "list-pipeline-execution-steps",
                         "--pipeline-execution-arn", target["arn"], "--sort-order", "Ascending"],
                        target["region"], profile)
        for s in listing.get("PipelineExecutionSteps") or []:
            meta = s.get("Metadata") or {}
            job_kind = job_arn = None
            for key, kind in STEP_JOB_KEYS:
                if isinstance(meta.get(key), dict) and meta[key].get("Arn"):
                    job_kind, job_arn = kind, meta[key]["Arn"]
                    break
            snap["steps"].append({
                "name": s.get("StepName"), "status": s.get("StepStatus"),
                "verdict": STEP_VERDICT.get(s.get("StepStatus"), "UNKNOWN"),
                "failure": s.get("FailureReason"),
                "failStep": "Fail" in meta,
                "errorMessage": (meta.get("Fail") or {}).get("ErrorMessage"),
                "jobKind": job_kind, "jobArn": job_arn, "start": s.get("StartTime"),
            })
    elif target["kind"] in LOG_GROUP:
        snap["jobKind"], snap["jobArn"] = target["kind"], target["arn"]
    return snap


def log_tail(kind: str, arn: str, since: str | None, region: str, profile: str | None,
             lines: int) -> dict:
    """The last lines a failed container wrote. `FailureReason` is often a truncation of it.

    Fetched rather than linked because the alternative is the console trip this exists to
    remove. Fail-soft: without `logs:FilterLogEvents` the command is printed instead.
    """
    if kind == "endpoint":
        group, prefix = f"/aws/sagemaker/Endpoints/{_job_name(arn)}", None
    elif kind in LOG_GROUP:
        group, prefix = LOG_GROUP[kind], _job_name(arn) + "/"
    else:
        return {}
    args = ["logs", "tail", group, "--format", "short", "--since", since or "7d"]
    if prefix:
        args[3:3] = ["--log-stream-name-prefix", prefix]
    shown = "aws " + shlex.join(args + ["--region", region]
                                + (["--profile", profile] if profile else []))
    try:
        out = _cli(args, region, profile, timeout=60)
    except AwsError as exc:
        return {"command": shown, "error": str(exc).splitlines()[0][:300]}
    tail = [l for l in out.splitlines() if l.strip()][-lines:]
    return {"command": shown, "lines": tail}


def explain(exc: AwsError, target: dict) -> str:
    """Turn a CLI error into what it means for the run. Shared with report.py."""
    first = str(exc).splitlines()[0] if str(exc) else exc.kind
    if exc.kind == "not-found":
        return (
            f"{target['arn']} does not exist in {target['region']} for these credentials.\n  "
            f"{first}\n  Either it was never created -- and then nothing was ever submitted -- "
            "or it belongs to another account: check the AWS_PROFILE: line in PLAN.md."
        )
    if exc.kind == "credentials":
        return (
            f"the credentials in use cannot read this execution.\n  {first}\n  It lives in "
            f"{target['partition']} / {target['region']}, account {target['account']}. Name "
            "the profile that submitted it with an `AWS_PROFILE: <name>` line in PLAN.md's "
            "header, or --profile. A machine holding several accounts rarely defaults to the "
            "right one."
        )
    return f"could not read {target['arn']}: {first}"


SETTLE_TRIES, SETTLE_SECONDS = 3, 3.0


def read(target: dict, profile: str | None) -> dict:
    """One snapshot, with any CLI error already turned into a Refusal that says what it means.

    A not-found is retried twice, a few seconds apart, before it counts. The first end-to-end
    run of this against a real account got `ResourceNotFound` for an execution that existed,
    and every read of it afterwards succeeded -- the cause was not found. A `[>]` reported
    seconds after its submit is the likeliest place for that to happen again. Six seconds is cheap next to refusing a real
    run as invented.
    """
    for attempt in range(SETTLE_TRIES):
        try:
            return snapshot(target, profile)
        except AwsError as exc:
            if exc.kind not in ("not-found", "transient") or attempt == SETTLE_TRIES - 1:
                raise Refusal(explain(exc, target)) from exc
            time.sleep(SETTLE_SECONDS)
    raise AssertionError("unreachable")


# ---------------------------------------------------------------- waiting


def _clock() -> str:
    return datetime.now().astimezone().strftime("%H:%M:%S")


def _age(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        mins = int((datetime.now().astimezone() - datetime.fromisoformat(iso)).total_seconds()
                   // 60)
    except (TypeError, ValueError):
        return ""
    return f"{mins // 60}h{mins % 60:02d}m" if mins >= 60 else f"{mins}m"


def _span(start: str | None, end: str | None) -> str:
    try:
        mins = int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
                   // 60)
    except (TypeError, ValueError):
        return ""
    return f"{mins // 60}h{mins % 60:02d}m" if mins >= 60 else f"{mins}m"


def _progress(snap: dict) -> str:
    steps = " ".join(f"{s['name']}={s['status']}" for s in snap["steps"])
    return f"{snap['status']}" + (f"  {steps}" if steps else "")


def wait(target: dict, profile: str | None, wait_min: float, interval: float,
         out) -> tuple[dict, int, float]:
    """Read until the run is terminal or this segment's budget is spent.

    A progress line is printed when anything changes and every five minutes otherwise, so a
    caller cut off by its own timeout still has the latest state in what it captured.
    Transient errors and not-founds are retried while the segment has time; three in a row,
    or any error that waiting cannot fix, end the segment with the error.
    """
    started = time.monotonic()
    deadline = started + wait_min * 60
    last: dict | None = None
    last_line, last_print, errors, checks = None, 0.0, 0, 0
    while True:
        try:
            snap = snapshot(target, profile)
            errors = 0
        except AwsError as exc:
            # not-found is retried as well as transient errors: see read() for why. A segment
            # with no time left raises at once, so --wait-min 0 still answers immediately.
            if (exc.kind not in ("transient", "not-found") or errors >= 2
                    or time.monotonic() >= deadline):
                raise
            errors += 1
            print(f"  {_clock()}  read failed, retrying: {str(exc).splitlines()[0][:160]}",
                  file=out, flush=True)
            snap = None
        checks += 1
        now = time.monotonic()
        if snap is not None:
            last = snap
            line = _progress(snap)
            if line != last_line or now - last_print >= 300:
                print(f"  {_clock()}  {line}", file=out, flush=True)
                last_line, last_print = line, now
            if snap["verdict"] in TERMINAL or snap["verdict"] == "UNKNOWN":
                return snap, checks, now - started
        if now >= deadline:
            if last is None:
                raise AwsError("no successful read of the execution in this segment", "transient")
            return last, checks, now - started
        time.sleep(max(0.0, min(interval, deadline - now)))


# ---------------------------------------------------------------- what to do next


def _load_lint():
    path = HERE / "plan-lint.py"
    spec = importlib.util.spec_from_file_location("plan_lint", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def failure_detail(snap: dict, profile: str | None, log_lines: int) -> dict:
    """What went wrong, in the words the service and the container used."""
    detail: dict = {"reason": snap.get("failure") or snap.get("exitMessage"), "steps": []}
    if snap["kind"] == "pipeline-execution":
        for s in snap["steps"]:
            if s["verdict"] not in ("FAILED", "STOPPED"):
                continue
            item = {"name": s["name"], "status": s["status"], "failStep": s["failStep"],
                    "reason": s.get("errorMessage") or s.get("failure")}
            if s.get("jobArn") and s["verdict"] == "FAILED":
                item["job"] = s["jobArn"]
                item["logs"] = log_tail(s["jobKind"], s["jobArn"], s.get("start"),
                                        snap["region"], profile, log_lines)
            detail["steps"].append(item)
    elif snap.get("jobArn") or snap["kind"] == "endpoint":
        detail["logs"] = log_tail(snap.get("jobKind") or snap["kind"], snap["arn"],
                                  snap.get("created"), snap["region"], profile, log_lines)
    return detail


def advice(snap: dict, verdict: str, ctx: dict) -> str:
    task, plan = ctx.get("task"), ctx.get("plan")
    report = f"python3 {HERE / 'report.py'} --plan {plan} --task {task}" if task else None
    again = ctx.get("again")

    if verdict == "RUNNING":
        return (
            f"not finished{f', so task {task} is not either' if task else ''}. Run this same "
            f"command again now, in this turn:\n      {again}\n"
            "  Do not end the turn on a submission, and do not hand the user a console link in "
            "place of a result: they asked for the outcome. Stop only if the user tells you to "
            "stop waiting -- then give them this ARN and say that next.py will dispatch this "
            "poll again when the run resumes."
        )

    if verdict == "SUCCEEDED":
        if snap["kind"] == "endpoint":
            return ("InService is what the endpoint says about itself. Send the smoke request "
                    "now and compare the endpoint's EndpointConfigName with the one the release "
                    "intended (release-and-serve, stage 14); only that counts as deployed.")
        if not report:
            return "the run finished. Fetch its outputs and check them before anyone relies on them."
        lines = []
        for t in ctx.get("siblings") or [ctx]:
            cmd = f"python3 {HERE / 'report.py'} --plan {plan} --task {t['task']} --state x"
            if t.get("produces"):
                cmd += f" --artifact <path to {t['produces']}>"
            if t.get("compute"):
                cmd += f" --compute \"{t['compute']}\""
            lines.append(cmd)
        shared = ctx.get("siblings") and len(ctx["siblings"]) > 1
        return (
            ("the run finished. " + ("These tasks share this execution; report each, in "
                                     "order:\n      " if shared else "Report it:\n      "))
            + "\n      ".join(lines)
            + "\n  Fetch each declared artefact from the run's outputs first -- report.py refuses "
              "[x] without it, and it reads this execution's status again itself. Then tell the "
              "user the outcome in the conversation, with the numbers, not a console link."
        )

    if verdict == "STOPPED":
        return (
            "somebody or something stopped this run. Tell the user, and ask whether to "
            "resubmit: a stop is a decision you did not make, and a resubmission bills again."
            + (f" Record it once they answer: {report} --state R" if report else "")
        )

    gate_only = bool(ctx.get("detail", {}).get("steps")) and all(
        s["failStep"] for s in ctx["detail"]["steps"])
    if gate_only:
        return (
            "the run ended at a FailStep -- a gate inside the pipeline refused, which is the "
            "pipeline working, not crashing. Tell the user which gate refused and the message "
            "above. Stages whose own steps succeeded can still be recorded: "
            + (f"{report} --state x --step <their step names>. " if report else "")
            + "The gate's own stage is [!], with `refused:` and a live `blocks:`."
        )
    return (
        "tell the user what failed, quoting the reason and the log lines above -- not a "
        "console link. "
        + (f"Record it: {report} --state R. " if report else "")
        + "Fix the cause before resubmitting: the same inputs fail the same way, and bill again."
    )


def render(snap: dict, verdict: str, ctx: dict, waited: float, checks: int) -> str:
    rows = [f"POLL  {snap['kind']}  {snap['arn']}"]
    if ctx.get("task"):
        rows.append(f"  task:      {ctx['task']} (stage {ctx.get('stage')})")
    if verdict in TERMINAL:
        took = _span(snap.get("created"), snap.get("modified"))
        when = f"   ran {took}" if took else ""
    else:
        age = _age(snap.get("created"))
        when = f"   started {age} ago" if age else ""
    rows.append(f"  status:    {snap['status']}{when}"
                + f"   ({checks} read(s) over {int(waited // 60)}m{int(waited % 60):02d}s)")
    if snap["steps"]:
        rows.append("  steps:     " + "  ".join(f"{s['name']}={s['status']}"
                                              for s in snap["steps"]))
    rows.append(f"VERDICT  {verdict}")
    d = ctx.get("detail") or {}
    if d.get("reason"):
        rows.append(f"  reason:    {d['reason']}")
    for s in d.get("steps") or []:
        what = "FailStep" if s["failStep"] else (_job_name(s["job"]) if s.get("job") else "")
        rows.append(f"  failed:    {s['name']} [{s['status']}]"
                    + (f" ({what})" if what else "") + (f": {s['reason']}" if s.get("reason")
                                                        else ""))
        rows += _render_logs(s.get("logs"))
    rows += _render_logs(d.get("logs"))
    rows.append("  do:        " + advice(snap, verdict, ctx))
    return "\n".join(rows)


def _render_logs(logs: dict | None) -> list[str]:
    if not logs:
        return []
    if logs.get("error"):
        return [f"  logs:      could not be read ({logs['error']})",
                f"             read them with: {logs['command']}"]
    body = logs.get("lines") or []
    if not body:
        return [f"  logs:      none found; {logs['command']}"]
    return ([f"  log tail:  last {len(body)} line(s) of {logs['command']}"]
            + [f"    | {l}" for l in body])


# ---------------------------------------------------------------- entry


def main() -> int:
    ap = argparse.ArgumentParser(description="Wait for a submitted SageMaker execution and "
                                             "say how it ended.")
    ap.add_argument("--plan", default="PLAN.md")
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--task", type=int, help="the [>] task whose execution to follow")
    who.add_argument("--execution", help="an ARN to follow directly, e.g. an endpoint")
    ap.add_argument("--profile", help="AWS profile; defaults to PLAN.md's AWS_PROFILE line")
    ap.add_argument("--wait-min", type=float, default=20.0,
                    help="how long this call waits before returning RUNNING (default 20)")
    ap.add_argument("--interval", type=float, default=60.0,
                    help="seconds between reads (default 60)")
    ap.add_argument("--log-lines", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        if a.wait_min < 0 or a.interval < 0:
            raise Refusal("--wait-min and --interval cannot be negative")
        plan = Path(a.plan)
        header: dict = {}
        tasks: list = []
        lint = None
        if plan.is_file():
            lint = _load_lint()
            header, tasks = lint.parse(plan.read_text(encoding="utf-8").splitlines())
        elif a.task is not None:
            raise Refusal(f"{plan} does not exist, so there is no task {a.task} to follow")

        ctx: dict = {"plan": str(plan)}
        if a.task is not None:
            task = next((t for t in tasks if t.num == a.task), None)
            if task is None:
                raise Refusal(f"PLAN.md has no task {a.task}")
            arn = task.execution
            if arn is None:
                raise Refusal(
                    f"task {a.task} records no `execution: <arn>`, so there is nothing to "
                    "follow. If it was submitted, report it [>] with the ARN the API returned; "
                    "if it was not, it is not waiting on anything remote."
                )
            stages = lint.load_stages(None).get("stages") or {}
            ctx.update(task=task.num, stage=task.stage)

            def one(t) -> dict:
                spec = stages.get(t.stage or "", {})
                m = COMPUTE_FIELD_RE.search(t.text)
                return {"task": t.num, "stage": t.stage, "produces": spec.get("produces"),
                        "compute": m["val"] if m else None}
            ctx.update(one(task))
            ctx["siblings"] = [one(t) for t in sorted(tasks, key=lambda t: t.num)
                               if t.marker == "[>]" and t.execution == arn] \
                or [one(task)]
            again = f"python3 {HERE / 'poll.py'} --plan {plan} --task {a.task}"
        else:
            arn = a.execution
            again = f"python3 {HERE / 'poll.py'} --execution {arn}"
        if a.profile:
            again += f" --profile {a.profile}"
        ctx["again"] = again

        target = parse_arn(arn)
        check_partition(target, header)
        profile = resolve_profile(a.profile, header)

        progress = sys.stderr if a.json else sys.stdout
        if not a.json:
            print(f"polling {target['kind']} {target['arn']} for up to {a.wait_min:g} min, "
                  f"every {a.interval:g}s", flush=True)
        try:
            snap, checks, waited = wait(target, profile, a.wait_min, a.interval, progress)
        except AwsError as exc:
            raise Refusal(explain(exc, target)) from exc

        verdict = snap["verdict"]
        if verdict == "UNKNOWN":
            raise Refusal(
                f"{arn} reports status {snap['status']!r}, which this script has no reading "
                "for. Refusing rather than guessing: an unrecognised status must not be taken "
                "for a finished run. Add it to VERDICT in poll.py once its meaning is known."
            )
        if verdict in ("FAILED", "STOPPED"):
            ctx["detail"] = failure_detail(snap, profile, a.log_lines)

        if a.json:
            print(json.dumps({"verdict": verdict, "snapshot": snap,
                              "detail": ctx.get("detail"), "reads": checks,
                              "waitedSeconds": round(waited, 1),
                              "do": advice(snap, verdict, ctx)},
                             indent=2, ensure_ascii=False, default=str))
        else:
            print(render(snap, verdict, ctx, waited, checks))
        return EXIT[verdict]

    except Refusal as exc:
        print(f"REFUSED  {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
