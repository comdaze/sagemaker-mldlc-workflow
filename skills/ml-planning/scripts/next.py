#!/usr/bin/env python3
"""Dispatch the next step of an MLDLC run, so the agent stops choosing the order.

Every failure this exists for was observed rather than imagined. A trial run compiled a
real SageMaker Pipeline, wrote 48 artefacts -- and its plan stopped at task 14 with stages
14, 15 and 16 simply gone. Not skipped, not deferred: absent, with contiguous numbering so
nothing looked wrong. The same run skipped batch inference, the deliverable it was for.

Those are not knowledge failures. That run had every rule available and had read the
scripts. They are failures of a loop whose exit condition the agent controls: nothing
anywhere said "you are not finished", because being finished was the agent's own call.

So this takes the call away. `next.py` says what to do next and `report.py` is the only
sanctioned way to write a task's state back. The agent's remaining job is to do the work
and say what happened -- which is what it is good at -- rather than to also decide whether
there is any work left, which is where it failed.

WHAT THIS CANNOT DO, said plainly because a control that oversells itself is worse than
none. It cannot stop an agent editing PLAN.md by hand: no script can. What it does instead
is make the edit NOT COUNT. The ledger holds a digest of the plan's semantic state, and a
plan that changed by any other route stops earning directives until it is reconciled. So
tampering does not get blocked, it costs the agent its own next instruction.

And it cannot help at all if nothing calls it. That is the same activation problem measured
at roughly one plain request in two, and a steering line remains the only answer to it.
This layer is for the run that HAS engaged and then loses the thread -- which, on the
evidence of two archived runs, is the failure that actually happens.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER_NAME = "PLAN.state.json"


class Refusal(Exception):
    """A condition under which no directive may be issued."""


def _load_sibling(name: str):
    """Import a sibling script whose filename is not an identifier."""
    import importlib.util

    path = HERE / name
    if not path.is_file():
        raise Refusal(f"{name} is not beside this script at {HERE}; cannot proceed")
    spec = importlib.util.spec_from_file_location(name.replace("-", "_")[:-3], path)
    if spec is None or spec.loader is None:
        raise Refusal(f"could not load {name}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- ledger


def semantic_digest(preset: str | None, tasks) -> str:
    """Digest the plan's STATE, not its prose.

    Deliberately excludes the wording of a reason, a question or a substitution: those
    belong to whoever wrote them and improving them must not raise an alarm. What it does
    cover is every structural fact -- the marker, the stage, and the numbers a rule reads.

    THE EXCLUSION WAS TOO WIDE, and a run showed why. It changed the S3 prefix its approved plan
    named and nothing objected: the markers were untouched, so the digest matched, so the approval
    that covered the old prefix went on covering the new one. The task TEXT is now in the digest,
    with the marker normalised out of it, so a substantive edit -- a bucket, a prefix, a target, a
    scope -- trips the same refusal a hand-edited marker does. `report.py` recomputes after its
    own writes, so ordinary progress still passes.
    """
    parts = [f"preset={preset or ''}"]
    for t in sorted(tasks, key=lambda t: t.num):
        substance = re.sub(r"\[(?: |-|\?|R|x|S|!|~|>)\]", "[]", t.text)
        parts.append(
            "|".join(
                [
                    str(t.num),
                    t.marker or "?multi",
                    t.stage or "-",
                    ",".join(str(n) for n in sorted(t.blocks)),
                    ",".join(str(n) for n in sorted(t.blocked_by)),
                    "asked" if t.asked else "",
                    "instead" if t.instead_of else "",
                    hashlib.sha256(substance.encode("utf-8")).hexdigest()[:16],
                ]
            )
        )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def load_ledger(path: Path) -> dict:
    if not path.is_file():
        return {"version": 1, "directives": 0, "dispatched": None, "digest": None,
                "off_dispatch": [], "history": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Refusal(
            f"{path} exists but could not be read ({exc}). Repair or delete it; a ledger "
            "that cannot be read must not be silently replaced, because that is how a "
            "run's history disappears."
        ) from exc


def save_ledger(path: Path, led: dict) -> None:
    path.write_text(json.dumps(led, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- gates


def run_lint(plan: Path, artifacts: Path | None) -> tuple[bool, str]:
    """Consistency is plan-lint.py's job. Delegate rather than grow a second rule set."""
    lint = HERE / "plan-lint.py"
    if not lint.is_file():
        raise Refusal(f"plan-lint.py is not beside this script at {HERE}")
    cmd = [sys.executable, str(lint), str(plan)]
    if artifacts is not None:
        cmd += ["--artifacts", str(artifacts)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def preset_stages(stages_doc: dict, preset: str) -> tuple[set[str], set[str]]:
    presets = stages_doc.get("presets") or {}
    if preset not in presets:
        known = ", ".join(sorted(presets)) or "(none)"
        raise Refusal(f"PLAN.md declares PRESET {preset!r}, which stages.toml does not "
                      f"define. Known presets: {known}")
    ext = (stages_doc.get("presets-satisfied-externally") or {}).get(preset) or []
    return {str(s) for s in presets[preset]}, {str(s) for s in ext}


# ---------------------------------------------------------------- dispatch


def absent_stages(tasks, wanted: set[str], external: set[str]) -> list[str]:
    """Preset stages that no task claims. Shared by the early gate and the dispatcher."""
    have = {t.stage for t in tasks if t.stage}
    return sorted((s for s in wanted if s not in have and s not in external),
                  key=lambda s: int(s) if s.isdigit() else 0)


def approval_gate(header: dict, tasks, stages_doc: dict) -> dict | None:
    """No execution work is dispatched until the plan has been approved once.

    `SKILL.md` has said "present the numbered plan for approval, then write it to PLAN.md"
    for as long as this skill has existed, and nothing ever checked it. So it held exactly
    as well as every other rule left in prose: a run wrote its plan and, in the same turn,
    delivered contracts, feature code, a trained model, an evaluation, a gate decision and a
    compiled Pipeline. The user's own account of it was "I hadn't told it to write code and
    it wrote all the code."

    Planning stages are exempt, because framing the problem and reading the environment are
    what produce the plan there is to approve. Everything else waits.

    The honest limit: an agent can write `APPROVED:` itself, so this is not proof of consent
    and is not presented as any. What it is: the claim becomes a quoted sentence attributed
    to the user, sitting in the file they are reading, which makes inventing one a visible
    lie rather than an omission nobody can see. Inside the loop it is a refusal, because
    nothing is dispatched without it.
    """
    val = (header.get("APPROVED") or (0, "none"))[1].strip().lower()
    if val not in ("", "none", "no", "pending"):
        return None

    stages = stages_doc.get("stages") or {}
    planning = {sid for sid, s in stages.items() if s.get("owner") == "ml-planning"}
    beyond = [
        t for t in sorted(tasks, key=lambda t: t.num)
        if t.stage and t.stage not in planning and t.marker != "[ ]"
    ]
    return {
        "action": "present-plan",
        "why": (
            "APPROVED is not set, so no execution work may be dispatched. "
            + (f"Tasks {', '.join(str(t.num) for t in beyond)} have already moved past "
               "[ ] without it." if beyond else
               "Nothing has started yet, which is the right moment for this.")
        ),
        "do": (
            "show the user the numbered plan and the scope preset, ask them to approve it, "
            "then record what they said: APPROVED: \"<their words>\" @ <ISO 8601>. "
            "Do not write that line on your own authority."
        ),
    }


def build_fingerprint() -> str:
    """A short digest of the control layer this process is actually running.

    Kiro COPIES a power into ~/.kiro/powers/installed at import time, so an edited clone and a
    running run can be different software with no sign of it anywhere. That is not theoretical:
    a validation run spent six hours exercising a snapshot taken before half the gates existed,
    and the only reason it came to light was a ledger field the newer code would have written.

    Nothing in a run could answer "which build am I on". Now the directive says so and the
    ledger keeps it, so a trial's own artefacts carry the version that produced them.
    """
    h = hashlib.sha256()
    for p in sorted(HERE.glob("*.py")) + [HERE.parent / "references" / "stages.toml"]:
        if p.is_file():
            h.update(p.name.encode("utf-8"))
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def choose(tasks, stages_doc, wanted: set[str], external: set[str], led: dict) -> dict:
    """Pick the one thing to do next, or refuse and say what is in the way."""
    lint_mod = _load_sibling("plan-lint.py")
    terminal = lint_mod.TERMINAL
    stages = stages_doc["stages"]

    by_stage: dict[str, list] = {}
    for t in tasks:
        if t.stage:
            by_stage.setdefault(t.stage, []).append(t)

    # main() asks this before the lint gate so the answer arrives as work rather than as a
    # violation; kept here too because choose() must be correct when called on its own.
    absent = absent_stages(tasks, wanted, external)
    if absent:
        return {
            "action": "repair-plan",
            "stages": absent,
            "why": (
                f"the preset includes stage(s) {', '.join(absent)} and PLAN.md has no task "
                "for them. A run whose plan lost its tail cannot be finished by continuing "
                "from the tail."
            ),
            "do": "add a task for each, then run next.py again",
        }

    settled = {s for s, ts in by_stage.items()
               if all(t.marker in terminal for t in ts)} | external

    # 2. Work already in hand outranks new work, in a fixed order: something local and
    #    unfinished, then something remote and unresolved, then a question outstanding.
    for marker, action, why, do in (
        ("[-]", "finish", "it is marked in progress locally, and at most one task may be",
         "complete it and report, or move it off [-] if it stalled"),
        ("[>]", "poll", "it was submitted to a remote executor and its result has not landed",
         "check the execution named in the task, then report its outcome"),
    ):
        hits = [t for t in sorted(tasks, key=lambda t: t.num) if t.marker == marker]
        if hits:
            t = hits[0]
            return {"action": action, "task": t.num, "stage": t.stage, "why": why, "do": do}

    open_q = [t for t in sorted(tasks, key=lambda t: t.num) if t.marker == "[?]"]
    if open_q:
        t = open_q[0]
        return {
            "action": "await-answer",
            "task": t.num,
            "stage": t.stage,
            "why": "a question is outstanding, and a run walked past one of these for eight "
                   "stages after the user had already replied",
            "do": "if the answer has arrived, report the task to [-], [x] or [S]; if not, "
                  "chase it. Only [~] may proceed alongside, and only by naming this blocker",
        }

    # 3. New work: lowest-numbered task whose stage's declared prerequisites are settled.
    #    Declared, not inferred -- stage 13 requires 10 and not 12.
    blocked: list[tuple[int, str, list[str]]] = []
    for t in sorted(tasks, key=lambda t: t.num):
        if t.marker in terminal or t.stage is None:
            continue
        if t.stage not in wanted:
            continue
        reqs = [str(r) for r in (stages.get(t.stage, {}).get("requires") or [])]
        unmet = [r for r in reqs if r not in settled]
        if unmet:
            blocked.append((t.num, t.stage, unmet))
            continue
        spec = stages.get(t.stage, {})
        return {
            "action": "execute",
            "task": t.num,
            "stage": t.stage,
            "skill": spec.get("owner"),
            "mode": spec.get("mode"),
            "execution": spec.get("execution"),
            "produces": spec.get("produces"),
            "satisfied_by": reqs,
            "why": f"lowest-numbered unsettled task whose prerequisites {reqs or '(none)'} "
                   "are all settled",
            "do": "do the work, then report it",
        }

    outstanding = [t for t in tasks if t.marker not in terminal and t.stage in wanted]
    if outstanding:
        chain = "; ".join(f"task {n} (stage {s}) waits on stage(s) {', '.join(u)}"
                          for n, s, u in blocked) or "no prerequisite chain explains it"
        raise Refusal(
            "there is unfinished work and nothing is dispatchable. " + chain + ". Either a "
            "prerequisite stage is missing from the preset or a settled stage was reported "
            "wrongly; next.py will not invent an order that the declared graph forbids."
        )

    unsettled_declared = sorted(wanted - settled, key=lambda s: int(s) if s.isdigit() else 0)
    if unsettled_declared:
        raise Refusal(
            f"every task is settled but stage(s) {', '.join(unsettled_declared)} in the "
            "preset are not. That is the shape of a plan whose tasks do not cover what it "
            "declared; run plan-lint.py --artifacts to see which."
        )
    return {"action": "complete",
            "why": "every stage the preset declares is settled",
            "do": "nothing; the run is done"}


# ---------------------------------------------------------------- output


def render(d: dict, n: int) -> str:
    if d["action"] in ("complete", "present-plan"):
        head = "COMPLETE" if d["action"] == "complete" else f"DIRECTIVE {n}  present-plan"
        return (f"{head}\n  build: {build_fingerprint()}  ({HERE})\n"
                f"  why: {d['why']}\n" + (f"  do:  {d['do']}" if d.get("do") else ""))
    if d["action"] == "repair-plan":
        return (f"DIRECTIVE {n}  repair-plan\n  build: {build_fingerprint()}\n"
                f"  stages missing: {', '.join(d['stages'])}\n"
                f"  why: {d['why']}\n  do:  {d['do']}")
    head = f"DIRECTIVE {n}  {d['action']}"
    rows = [("build", build_fingerprint()),
            ("task", d.get("task")), ("stage", d.get("stage")), ("skill", d.get("skill")),
            ("execution", d.get("execution")), ("mode", d.get("mode")),
            ("produces", d.get("produces"))]
    body = "\n".join(f"  {k+':':<11}{v}" for k, v in rows if v is not None)
    tail = f"\n  why:       {d['why']}\n  do:        {d['do']}"
    if d.get("task") is not None:
        tail += (f"\n  report:    python3 {HERE / 'report.py'} --task {d['task']} "
                 "--state <marker> [--artifact PATH]")
    return head + "\n" + body + tail


def main() -> int:
    ap = argparse.ArgumentParser(description="Issue the next directive for an MLDLC run.")
    ap.add_argument("plan", nargs="?", default="PLAN.md")
    ap.add_argument("--artifacts", help="artefact directory, passed through to plan-lint")
    ap.add_argument("--stages", help="path to stages.toml")
    ap.add_argument("--json", action="store_true", help="emit the directive as JSON")
    ap.add_argument("--no-record", action="store_true",
                    help="show what would be dispatched without recording it")
    args = ap.parse_args()

    try:
        plan = Path(args.plan)
        if not plan.is_file():
            raise Refusal(f"{plan} does not exist. There is no run to advance; use the "
                          "ml-planning skill to write a plan first.")
        lint_mod = _load_sibling("plan-lint.py")
        stages_doc = lint_mod.load_stages(Path(args.stages) if args.stages else None)

        header, tasks = lint_mod.parse(plan.read_text(encoding="utf-8").splitlines())
        if "PRESET" not in header:
            raise Refusal("PLAN.md declares no PRESET, so there is no set of stages to "
                          "advance through. Add `PRESET: <name>` to the header.")
        preset = header["PRESET"][1].strip()
        wanted, external = preset_stages(stages_doc, preset)

        artifacts = Path(args.artifacts) if args.artifacts else None

        # Coverage is asked BEFORE the lint gate, on purpose. plan-lint.py refuses a plan
        # whose preset has uncovered stages, so without this the round-10 failure -- three
        # stages gone from the tail -- surfaces as "2 violation(s)" rather than as work to
        # do. This layer exists to hand out an instruction, not a complaint, and a weaker
        # model is exactly the one that can act on "add tasks for 14, 15, 16" and flounder
        # on a violation dump. lint remains the authority: it would refuse this plan too.
        early = absent_stages(tasks, wanted, external)
        if early:
            d = {
                "action": "repair-plan",
                "stages": early,
                "why": f"PRESET {preset} includes stage(s) {', '.join(early)} and PLAN.md has "
                       "no task for them. A run whose plan lost its tail cannot be finished "
                       "by continuing from the tail.",
                "do": "add one task per missing stage, each with its `_(Stage: N | Skill: "
                      "X)_` attribution, then run next.py again",
            }
            ledger = plan.with_name(LEDGER_NAME)
            led = load_ledger(ledger)
            if not args.no_record:
                led["directives"] += 1
                led["build"] = build_fingerprint()
                led["dispatched"] = {"n": led["directives"], "action": "repair-plan",
                                     "task": None, "stages": early}
                led.setdefault("history", []).append(led["dispatched"])
                # Adding tasks IS a structural edit of PLAN.md, and this directive is what
                # asked for it -- so the digest is surrendered rather than enforced. An
                # engine that demands a change and then calls that change tampering has
                # built a trap, not a control.
                led["digest"] = None
                save_ledger(ledger, led)
            print(json.dumps(d, indent=2, ensure_ascii=False) if args.json
                  else render(d, led["directives"]))
            return 0

        ok, out = run_lint(plan, artifacts)
        if not ok:
            raise Refusal("plan-lint.py refuses this plan, so no directive is issued -- "
                          "advancing a plan that fails its own checks buries the fault "
                          "under later work.\n" + out)

        # After lint, before dispatch: an unapproved plan gets no execution directive.
        gate = approval_gate(header, tasks, stages_doc)
        if gate is not None:
            ledger = plan.with_name(LEDGER_NAME)
            led = load_ledger(ledger)
            if not args.no_record:
                led["directives"] += 1
                led["build"] = build_fingerprint()
                led["dispatched"] = {"n": led["directives"], "action": "present-plan",
                                     "task": None}
                led.setdefault("history", []).append(led["dispatched"])
                save_ledger(ledger, led)
            print(json.dumps(gate, indent=2, ensure_ascii=False) if args.json
                  else render(gate, led["directives"]))
            return 0

        ledger = plan.with_name(LEDGER_NAME)
        led = load_ledger(ledger)
        digest = semantic_digest(preset, tasks)
        if led.get("digest") and led["digest"] != digest:
            raise Refusal(
                "the plan's state changed by some route other than report.py, so the ledger "
                "no longer describes it.\n  ledger: " + led["digest"][:16] +
                "\n  plan:   " + digest[:16] +
                "\nNothing is broken and nothing is accused: a hand edit is allowed, it just "
                "does not count. Re-record the change with report.py, or delete "
                f"{LEDGER_NAME} to restart the ledger from where the plan now stands."
            )

        d = choose(tasks, stages_doc, wanted, external, led)
        n = led["directives"] + (0 if d["action"] == "complete" else 1)

        if not args.no_record and d["action"] != "complete":
            led["directives"] = n
            led["digest"] = digest
            led["build"] = build_fingerprint()
            led["dispatched"] = {"n": n, "action": d["action"], "task": d.get("task"),
                                 "stage": d.get("stage")}
            led.setdefault("history", []).append(led["dispatched"])
            save_ledger(ledger, led)
        elif d["action"] == "complete" and not args.no_record:
            led["digest"] = digest
            led["dispatched"] = None
            save_ledger(ledger, led)

        print(json.dumps(d, indent=2, ensure_ascii=False) if args.json else render(d, n))
        return 0

    except Refusal as exc:
        print(f"REFUSED  {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
