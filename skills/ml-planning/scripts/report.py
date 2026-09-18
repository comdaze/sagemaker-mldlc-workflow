#!/usr/bin/env python3
"""Record one step's outcome. The only sanctioned writer of task state in PLAN.md.

`next.py` decides what happens next; this decides nothing. It takes a claim about what
happened, refuses the claims that the stage's own declaration forbids, and writes the rest.

Three properties, in the order they matter.

FIRST, IT WRITES AND THEN VERIFIES, AND REVERTS IF IT MADE THINGS WORSE. Every write is
followed by plan-lint.py on the result, and a write that introduces a violation is rolled
back with the violation quoted. So the plan is lint-clean after every report, not merely
after the reports someone remembered to check. A tool that can leave the state invalid is a
tool that will.

SECOND, THE CLAIMS IT REFUSES COME FROM stages.toml, NOT FROM HERE. `[S]` on an ALWAYS
stage is refused; on a DELIVERABLE it needs `--waived-by user`; `[>]` is refused on a stage
whose mode is not `pipeline`; `[x]` is refused when the artefact the stage declares it
produces is not on disk. That last one is the one worth naming: a run marked five tasks
complete, and the reason the plan passed was that nothing compared the claim to the
workspace.

THIRD, WORKING OUT OF ORDER IS RECORDED, NOT BLOCKED. A report for a task other than the
dispatched one is accepted when the graph permits it, and the ledger keeps a count. The
distinction is deliberate and matches how this power classifies its own constraints: a
refusal is for what must not happen, and accounting is for what must not happen silently.
Refusing every off-dispatch step would make the tool something to work around, and a
control that gets bypassed protects nothing.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER_NAME = "PLAN.state.json"
STATES = ("x", "S", "!", "~", ">", "-", "?", "R", " ")


class Refusal(Exception):
    pass


def _sibling(name: str):
    path = HERE / name
    if not path.is_file():
        raise Refusal(f"{name} is not beside this script at {HERE}")
    spec = importlib.util.spec_from_file_location(name.replace("-", "_")[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- validation


def validate(state: str, spec: dict, a, stage: str) -> list[str]:
    """Return the field text to append, refusing what the stage's declaration forbids."""
    execution = spec.get("execution", "CONDITIONAL")
    fields: list[str] = []

    if state == "S":
        if execution == "ALWAYS":
            raise Refusal(
                f"stage {stage} is declared ALWAYS, so it cannot be skipped -- not with a "
                "reason, and not by the user. A gate a reason can wave away is not a "
                "refusal, it is a suggestion with paperwork."
            )
        if execution == "DELIVERABLE":
            if (a.waived_by or "").strip().lower() != "user":
                raise Refusal(
                    f"stage {stage} is declared DELIVERABLE -- it is a thing the user asked "
                    "for. Skipping it needs their explicit say-so: pass --waived-by user "
                    "once you have it. A run skipped batch inference, the 96 predictions it "
                    "was for, because the implementation was not ready yet; that is a "
                    "reason to finish the work, not a permit to drop it."
                )
            fields.append(f"waived-by: user. skipped: {a.reason}" if a.reason
                          else "waived-by: user. skipped: waived by the user")
        else:
            if not a.reason:
                raise Refusal(f"[S] on stage {stage} needs --reason; an unexplained skip is "
                              "indistinguishable from an oversight")
            fields.append(f"skipped: {a.reason}")

    elif state == "x":
        produces = spec.get("produces")
        if produces:
            given = [Path(p) for p in (a.artifact or [])]
            found = next((p for p in given if p.is_file()), None)
            if found is None:
                looked = ", ".join(str(p) for p in given) or "(none given)"
                raise Refusal(
                    f"stage {stage} declares it produces `{produces}`, and no existing file "
                    f"was given to stand for it -- checked: {looked}. A claim of completion "
                    "that the workspace does not corroborate is the one thing this refuses "
                    "outright: a run marked five tasks complete and passed, because nothing "
                    "compared the claim to the disk."
                )

    elif state == ">":
        if spec.get("mode") != "pipeline":
            raise Refusal(
                f"stage {stage} declares mode {spec.get('mode')!r}, so there is no remote "
                "execution to await. [>] means submitted to an executor; for local work in "
                "hand the state is [-]."
            )
        if not a.execution:
            raise Refusal("[>] needs --execution naming the run. An id that exists can be "
                          "checked later; one that does not was invented.")
        fields.append(f"execution: {a.execution}")

    elif state == "!":
        if not a.refused:
            raise Refusal("[!] needs --refused pointing at the artefact holding the "
                          "decision. A refusal with no artefact is a claim.")
        if not a.blocks:
            raise Refusal("[!] needs --blocks listing what the refusal still stops. A "
                          "refusal that blocks nothing did not refuse anything.")
        fields.append(f"refused: {a.refused}. blocks: {a.blocks}")

    elif state == "~":
        if not (a.instead_of and a.blocked_by):
            raise Refusal("[~] needs --instead-of (what was delivered instead of what was "
                          "asked) and --blocked-by (the task that justifies it)")
        fields.append(f"instead-of: {a.instead_of}. blocked-by: {a.blocked_by}")

    elif state == "?":
        if not a.asked:
            raise Refusal("[?] needs --asked recording what you put to the user. A blocker "
                          "nobody was told about is not a blocker, it is an assumption.")
        fields.append(f"asked: {a.asked}")

    return fields


# ---------------------------------------------------------------- writing


def rewrite(text: str, num: int, state: str, fields: list[str], lint_mod) -> str:
    lines = text.splitlines(keepends=True)
    target = None
    in_fence = False
    for i, raw in enumerate(lines):
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = lint_mod.TASK_RE.match(raw.rstrip("\n"))
        if m and int(m.group("num")) == num:
            target = i
            break
    if target is None:
        raise Refusal(f"PLAN.md has no task {num}. next.py --json names the task to report; "
                      "reporting one that does not exist would create state for no work.")

    line = lines[target].rstrip("\n")
    if not lint_mod.MARKER_RE.search(line):
        raise Refusal(f"task {num} carries no state marker, so there is nothing to move. "
                      "Give it one in PLAN.md first.")
    line = lint_mod.MARKER_RE.sub(f"[{state}]", line, count=1)

    if fields:
        add = " ".join(fields)
        attr = line.find("_(Stage:")
        line = (line[:attr].rstrip() + f" {add} " + line[attr:]) if attr != -1 \
            else line.rstrip() + f" {add}"
    lines[target] = line + "\n"

    # LAST_DONE must agree with the highest [x], so recompute it rather than trusting it.
    _, tasks = lint_mod.parse([ln.rstrip("\n") for ln in lines])
    done = [t.num for t in tasks if t.marker == "[x]"]
    if done:
        want = f"LAST_DONE: {max(done)} @ {now()}"
        for i, raw in enumerate(lines):
            if lint_mod.LAST_DONE_RE.match(raw.rstrip("\n")):
                lines[i] = want + "\n"
                break
    return "".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Record one step's outcome in PLAN.md.")
    ap.add_argument("--task", type=int, required=True)
    ap.add_argument("--state", required=True, choices=STATES)
    ap.add_argument("--plan", default="PLAN.md")
    ap.add_argument("--artifact", action="append", help="file evidencing the work; repeatable")
    ap.add_argument("--artifacts", help="artefact directory, passed through to plan-lint")
    ap.add_argument("--stages")
    ap.add_argument("--reason"), ap.add_argument("--waived-by", dest="waived_by")
    ap.add_argument("--execution"), ap.add_argument("--refused")
    ap.add_argument("--blocks"), ap.add_argument("--instead-of", dest="instead_of")
    ap.add_argument("--blocked-by", dest="blocked_by"), ap.add_argument("--asked")
    a = ap.parse_args()

    try:
        plan = Path(a.plan)
        if not plan.is_file():
            raise Refusal(f"{plan} does not exist")
        lint_mod = _sibling("plan-lint.py")
        nxt = _sibling("next.py")
        stages_doc = lint_mod.load_stages(Path(a.stages) if a.stages else None)

        original = plan.read_text(encoding="utf-8")
        header, tasks = lint_mod.parse(original.splitlines())
        task = next((t for t in tasks if t.num == a.task), None)
        if task is None:
            raise Refusal(f"PLAN.md has no task {a.task}")
        stage = task.stage
        if stage is None:
            raise Refusal(f"task {a.task} declares no `Stage:`, so no stage's rules can be "
                          "applied to it. Attribution is derived from the stage, so a task "
                          "without one is outside every rule this checks.")
        spec = (stages_doc.get("stages") or {}).get(stage)
        if spec is None:
            raise Refusal(f"task {a.task} names stage {stage}, which stages.toml does not "
                          "declare")

        fields = validate(a.state, spec, a, stage)
        updated = rewrite(original, a.task, a.state, fields, lint_mod)
        plan.write_text(updated, encoding="utf-8")

        ok, out = nxt.run_lint(plan, Path(a.artifacts) if a.artifacts else None)
        if not ok:
            plan.write_text(original, encoding="utf-8")
            raise Refusal("that report would leave the plan failing its own checks, so it "
                          "was rolled back and PLAN.md is unchanged.\n" + out)

        ledger = plan.with_name(LEDGER_NAME)
        led = nxt.load_ledger(ledger)
        dispatched = led.get("dispatched") or {}
        off = dispatched.get("task") not in (None, a.task)
        if off:
            led.setdefault("off_dispatch", []).append(
                {"reported": a.task, "dispatched": dispatched.get("task"), "at": now()}
            )
        _, new_tasks = lint_mod.parse(updated.splitlines())
        led["digest"] = nxt.semantic_digest(
            header.get("PRESET", (0, None))[1], new_tasks
        )
        led["dispatched"] = None
        led.setdefault("reports", []).append(
            {"task": a.task, "state": a.state, "stage": stage, "at": now(),
             "off_dispatch": off}
        )
        nxt.save_ledger(ledger, led)

        print(f"RECORDED  task {a.task} -> [{a.state}]  (stage {stage}, "
              f"{spec.get('execution')})")
        if fields:
            print("  " + " ".join(fields))
        if off:
            n = len(led["off_dispatch"])
            print(f"  NOTE  directive {dispatched.get('n')} asked for task "
                  f"{dispatched.get('task')}, and this reports task {a.task}. Allowed by the "
                  f"graph and recorded; {n} off-dispatch report(s) so far.")
        print(f"  next: python3 {HERE / 'next.py'} {plan}")
        return 0

    except Refusal as exc:
        print(f"REFUSED  {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
