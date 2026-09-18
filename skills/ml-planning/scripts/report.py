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
            check_verdict(found, spec, stage)

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


def check_verdict(path: Path, spec: dict, stage: str) -> None:
    """Read the artefact's own verdict, and refuse `[x]` when the gate said no.

    The existence check that came first asked whether the file was there and never what it
    said. That left the largest hole in the power: a stage whose gate REFUSED could still be
    recorded complete. It was verified by setting a real run's `leakage-audit.json` to
    `status: REFUSED` with three refused features -- report.py answered
    `RECORDED task 4 -> [x] (stage 4, ALWAYS)` without comment. The same held for the quality
    gate, which is the constraint this entire power is organised around.

    So on every run so far, a gate held because the agent chose to honour it. One did choose
    correctly, marking the refused gate `[!]` with a reason and a live blocks list. Goodwill
    that happens to be sound is still goodwill, and this is the difference between a refusal
    and advice.

    The field to read is declared per stage in stages.toml, because these artefacts do not
    share a schema: one carries `status: "REFUSED"`, another `registrationAllowed: false`.
    Guessing across them would mean either missing a refusal or inventing one.
    """
    v = spec.get("verdict")
    if not isinstance(v, dict):
        return
    field, refused = v.get("field"), v.get("refused")
    if not field:
        return
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Refusal(
            f"stage {stage} declares its verdict lives in `{field}` of its artefact, and "
            f"{path} could not be read as JSON ({exc}). An artefact whose verdict cannot be "
            "read cannot be treated as a pass."
        ) from exc
    if not isinstance(doc, dict) or field not in doc:
        raise Refusal(
            f"stage {stage} declares its verdict lives in `{field}`, and {path.name} does "
            f"not contain it. Either the gate did not write this artefact or it is a "
            "different document; both mean the stage cannot be recorded complete on it."
        )
    if doc[field] == refused:
        raise Refusal(
            f"the artefact for stage {stage} records its own refusal: `{field}` is "
            f"{doc[field]!r}. The gate ran and said no, so the stage is not complete.\n"
            f"  Report it as [!] instead, with `refused:` pointing at {path.name} and a "
            "`blocks:` list naming what the refusal still stops. That is what the state is "
            "for, and it keeps the refusal visible instead of closing over it."
        )


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


def check_permission(task, tasks, header: dict, stages_doc: dict, state: str) -> None:
    """Refuse before writing, not after -- two things plan-lint cannot catch on its own.

    The write-then-relint-then-revert cycle already stops an invalid plan reaching disk. It
    is not enough here, for two reasons that are the same reason twice: it reports a generic
    "that would fail the checks" where the actual answer is "the user has not agreed to
    this", and there are two shapes it misses entirely.

    FIRST, A HARD HOLD. A run put one task at `[?]` asking for a bucket and an execution
    role, then reported eight downstream tasks and delivered a whole implementation. Every
    one named the blocker, which was exactly what the rules asked for, so nothing objected.
    `stages.toml` now marks such a stage `holds = "hard"`, and while its task is `[?]` no
    downstream task may move at all -- including to `[S]`, which plan-lint does not treat as
    progress and which would otherwise let a run dispose of blocked work unilaterally.
    Reporting the held task ITSELF stays legal: that is how the block clears once the user
    answers.

    SECOND, AN UNAPPROVED PLAN. `next.py` will not dispatch execution work without an
    `APPROVED:` line, but nothing stopped an agent skipping the dispatcher and calling this
    directly -- and this is the only sanctioned writer, so that route has to be closed here
    or the gate is decorative. Planning stages are exempt: they produce the plan there is to
    approve.
    """
    spec = stages_doc.get("stages") or {}
    reported = task.stage

    for t in tasks:
        if t.marker != "[?]" or t.num >= task.num:
            continue
        if spec.get(t.stage or "", {}).get("holds") != "hard":
            continue
        raise Refusal(
            f"task {t.num} (stage {t.stage}) is [?] and that stage declares "
            f'holds = "hard", so task {task.num} may not move to [{state}]. What it waits '
            "on is something only the user can supply or authorise, so doing the "
            "downstream work -- or deciding to skip it -- is acting on permission you do "
            f"not have.\n  To clear this: get the answer, then report task {t.num} itself. "
            "That is the one report this allows while the hold stands."
        )

    approved = (header.get("APPROVED") or (0, "none"))[1].strip().lower()
    planning = {sid for sid, s in spec.items() if s.get("owner") == "ml-planning"}
    if approved in ("", "none", "no", "pending") and reported not in planning:
        raise Refusal(
            f"the plan has no APPROVED line, so stage {reported} may not be recorded as "
            f"[{state}]. Present the numbered plan, ask the user to approve it, and record "
            'what they said: APPROVED: "<their words>" @ <ISO 8601>. Do not write that line '
            "on your own authority.\n  A run wrote its plan and in the same turn delivered "
            "contracts, feature code, a trained model, an evaluation and a compiled "
            "Pipeline. The rule requiring approval had existed all along, as prose."
        )


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

        check_permission(task, tasks, header, stages_doc, a.state)
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
