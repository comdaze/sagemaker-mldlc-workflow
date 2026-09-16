#!/usr/bin/env python3
"""Lint a PLAN.md written by the ml-planning skill.

The plan file is the state of the work, not a summary of it, so it has to be
checkable by something other than the agent that wrote it. Every check below
corresponds to a rule stated in `ml-planning/SKILL.md`: a rule nothing can
check is a rule that decays into a suggestion, which is the failure mode this
script exists to close.

Exit 0 when the plan is clean, 1 when it is not. Standard library only.

    python3 plan-lint.py PLAN.md
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

# The seven states. Anything else in a task's marker position is a violation
# rather than something to interpret generously.
STATES = {
    "[ ]": "not started",
    "[-]": "in progress",
    "[?]": "awaiting a human decision",
    "[R]": "revising after a failed gate or review",
    "[x]": "done",
    "[S]": "skipped -- a decision not to run it",
    "[!]": "ran, refused, and the refusal stands while work continued",
}

# A task that will never be worked on again. Ordering is checked against these
# together, because neither a skipped nor a refused-and-waived task should block
# the ones after it -- but [!] carries its own blocking list, checked separately.
TERMINAL = {"[x]", "[S]", "[!]"}

PARTITIONS = {"aws", "aws-cn", "aws-us-gov"}

TASK_RE = re.compile(r"^(?P<num>\d+)\.\s+(?P<rest>.*)$")
MARKER_RE = re.compile(r"\[(?: |-|\?|R|x|S|!)\]")
SKILL_RE = re.compile(r"\bSkill:\s*(?P<name>[A-Za-z0-9._-]+)\s*\)_")
# A stage no skill in this power owns yet. The would-be owner must be named, so
# the gap is a field a reader can count rather than a sentence in prose.
UNOWNED_RE = re.compile(
    r"\bSkill:\s*none\s*;\s*would\s+be:\s*(?P<name>[A-Za-z0-9._-]+)\s*\)_"
)
STAGE_RE = re.compile(r"_\(Stage:\s*(?P<stage>[A-Za-z0-9._-]+)\s*\|")
BLOCKS_RE = re.compile(r"blocks:\s*(?P<nums>\d+(?:\s*,\s*\d+)*)")
LAST_DONE_RE = re.compile(r"^LAST_DONE:\s*(?P<val>.+?)\s*$")
PARTITION_RE = re.compile(r"^PARTITION:\s*(?P<val>.+?)\s*$")
LAST_DONE_VALUE_RE = re.compile(r"^(?P<num>\d+)\s*@\s*(?P<ts>\S+)$")


class Report:
    """Collects violations so the run reports all of them, not just the first."""

    def __init__(self) -> None:
        self.violations: list[str] = []

    def fail(self, check: str, line_no: int | None, message: str) -> None:
        where = f"line {line_no}: " if line_no else ""
        self.violations.append(f"[{check}] {where}{message}")

    @property
    def ok(self) -> bool:
        return not self.violations


class Task:
    def __init__(self, num: int, line_no: int, text: str) -> None:
        self.num = num
        self.line_no = line_no
        self.text = text
        self.marker_list = ["[" + m + "]" for m in _raw_markers(text)]
        # An unowned attribution is matched first and removed, so its "none"
        # does not also register as an ordinary skill name.
        self.unowned = UNOWNED_RE.findall(text)
        self.skills = SKILL_RE.findall(UNOWNED_RE.sub("", text))
        m = STAGE_RE.search(text)
        self.stage = m.group("stage") if m else None
        m = BLOCKS_RE.search(text)
        self.blocks = (
            [int(n.strip()) for n in m.group("nums").split(",")] if m else []
        )

    @property
    def marker(self) -> str | None:
        return self.marker_list[0] if len(self.marker_list) == 1 else None


def _raw_markers(text: str) -> list[str]:
    """Return the inner character of every state-marker-shaped token."""
    return [m.group(0)[1:-1] for m in MARKER_RE.finditer(text)]


def discover_skills(explicit: Path | None) -> set[str]:
    """Names of the skills in this power.

    A failure to locate them is a HARD failure, never a warning: the
    plan-names-a-real-skill check is the most valuable one here, and a linter
    that reports success while silently skipping its main check is worse than
    no linter.
    """
    if explicit is not None:
        skills_dir = explicit
    else:
        # .../skills/ml-planning/scripts/plan-lint.py -> .../skills
        skills_dir = Path(__file__).resolve().parent.parent.parent

    if not skills_dir.is_dir():
        raise SystemExit(
            f"plan-lint: cannot read the skills directory at {skills_dir}.\n"
            "Pass --skills-dir pointing at this power's skills/ directory. "
            "Refusing to run without it, because the skill-name check would be "
            "silently skipped."
        )

    names = {p.name for p in skills_dir.iterdir() if (p / "SKILL.md").is_file()}
    if not names:
        raise SystemExit(
            f"plan-lint: no skills found under {skills_dir} (looked for */SKILL.md). "
            "Refusing to run rather than skip the skill-name check."
        )
    return names


def parse(lines: list[str]) -> tuple[dict[str, tuple[int, str]], list[Task]]:
    """Split the plan into its header fields and its numbered tasks."""
    header: dict[str, tuple[int, str]] = {}
    tasks: list[Task] = []
    in_fence = False

    for line_no, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")

        # A fenced block is an example, not the plan. Skip it so a template
        # quoted inside the plan cannot be mistaken for real tasks.
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        m = LAST_DONE_RE.match(line)
        if m:
            header.setdefault("LAST_DONE", (line_no, m.group("val")))
            continue
        m = PARTITION_RE.match(line)
        if m:
            header.setdefault("PARTITION", (line_no, m.group("val")))
            continue

        m = TASK_RE.match(line)
        if m:
            tasks.append(Task(int(m.group("num")), line_no, m.group("rest")))

    return header, tasks


def check_header(header: dict[str, tuple[int, str]], tasks: list[Task], r: Report) -> None:
    if "PARTITION" not in header:
        r.fail(
            "header",
            None,
            "no PARTITION line. Step 1 resolves the partition; a resumed session "
            "that cannot read it will assume the global one.",
        )
    else:
        line_no, value = header["PARTITION"]
        if value not in PARTITIONS:
            r.fail(
                "header",
                line_no,
                f"PARTITION is {value!r}; expected one of {sorted(PARTITIONS)}.",
            )

    if "LAST_DONE" not in header:
        r.fail("header", None, "no LAST_DONE line; a resumed session has no cursor.")
        return

    line_no, value = header["LAST_DONE"]
    done_nums = [t.num for t in tasks if t.marker == "[x]"]
    highest = max(done_nums) if done_nums else None

    if value.strip() == "none":
        if highest is not None:
            r.fail(
                "cursor",
                line_no,
                f"LAST_DONE is none but task {highest} is marked [x].",
            )
        return

    m = LAST_DONE_VALUE_RE.match(value)
    if not m:
        r.fail(
            "cursor",
            line_no,
            f"LAST_DONE is {value!r}; expected '<task number> @ <ISO 8601>' or 'none'.",
        )
        return

    try:
        datetime.fromisoformat(m.group("ts"))
    except ValueError:
        r.fail("cursor", line_no, f"LAST_DONE timestamp {m.group('ts')!r} is not ISO 8601.")

    claimed = int(m.group("num"))
    if highest is None:
        r.fail("cursor", line_no, f"LAST_DONE names task {claimed} but no task is [x].")
    elif claimed != highest:
        r.fail(
            "cursor",
            line_no,
            f"LAST_DONE names task {claimed}; the highest [x] task is {highest}.",
        )


def check_numbering(tasks: list[Task], r: Report) -> None:
    if not tasks:
        r.fail("numbering", None, "the plan has no numbered tasks.")
        return
    seen: dict[int, int] = {}
    for t in tasks:
        if t.num in seen:
            r.fail("numbering", t.line_no, f"task {t.num} is numbered twice (also line {seen[t.num]}).")
        seen[t.num] = t.line_no
    expected = list(range(1, len(tasks) + 1))
    actual = [t.num for t in tasks]
    if actual != expected:
        r.fail(
            "numbering",
            tasks[0].line_no,
            f"task numbers are {actual}; expected a contiguous {expected}.",
        )


def check_markers(tasks: list[Task], r: Report) -> None:
    for t in tasks:
        if not t.marker_list:
            r.fail("marker", t.line_no, f"task {t.num} carries no state marker.")
        elif len(t.marker_list) > 1:
            r.fail(
                "marker",
                t.line_no,
                f"task {t.num} carries {len(t.marker_list)} state markers "
                f"({' '.join(t.marker_list)}); exactly one is required.",
            )

    in_progress = [t for t in tasks if t.marker == "[-]"]
    if len(in_progress) > 1:
        r.fail(
            "marker",
            in_progress[1].line_no,
            "tasks "
            + ", ".join(str(t.num) for t in in_progress)
            + " are all [-]; this plan runs one task at a time.",
        )


def check_ordering(tasks: list[Task], r: Report) -> None:
    """A task is only done if everything it depends on is settled.

    The plan's ordering is a prerequisite chain (each step's output is the next
    step's input), so a completed task sitting above an unsettled one means
    either the plan lied or the work skipped an input.
    """
    for i, t in enumerate(tasks):
        if t.marker != "[x]":
            continue
        for earlier in tasks[:i]:
            if earlier.marker not in TERMINAL:
                r.fail(
                    "ordering",
                    t.line_no,
                    f"task {t.num} is [x] while task {earlier.num} is "
                    f"{earlier.marker or 'unmarked'} — its input is not settled.",
                )
                break


def check_skips(tasks: list[Task], r: Report) -> None:
    for t in tasks:
        if t.marker != "[S]":
            continue
        if not re.search(r"skipped:\s*\S", t.text):
            r.fail(
                "skip",
                t.line_no,
                f"task {t.num} is [S] with no reason; write 'skipped: <why>' so a "
                "later reader knows whether the skip still holds.",
            )


def check_refusals(tasks: list[Task], r: Report) -> None:
    """[!] means a gate ran and refused, and work continued anyway.

    [S] cannot carry this. A skip reads as "did not happen", which is the
    opposite of "happened and said no" -- and a real run used [S] for a refused
    quality gate, which made a blocked release look like a scope decision in the
    linter's own summary line.

    So [!] is a separate state with two obligations: point at the artefact
    holding the refusal, and name the tasks the refusal still blocks. The second
    is the one with teeth -- every task listed must be unfinished, which is what
    stops a waived gate from quietly releasing a model.
    """
    by_num = {t.num: t for t in tasks}
    for t in tasks:
        if t.marker != "[!]":
            continue

        if not re.search(r"refused:\s*\S", t.text):
            r.fail(
                "refusal",
                t.line_no,
                f"task {t.num} is [!] with no 'refused: <what the gate decided, and "
                "where the evidence is>'. A refusal with no artefact is a claim.",
            )

        if not t.blocks:
            r.fail(
                "refusal",
                t.line_no,
                f"task {t.num} is [!] but names no 'blocks: <task numbers>'. A "
                "refusal that blocks nothing is a refusal that was waived in full; "
                "say so with [S] and a reason instead.",
            )

        for n in t.blocks:
            if n not in by_num:
                r.fail(
                    "refusal",
                    t.line_no,
                    f"task {t.num} blocks task {n}, which does not exist.",
                )
            elif by_num[n].marker in {"[x]", "[S]"}:
                r.fail(
                    "refusal",
                    t.line_no,
                    f"task {t.num} refused and blocks task {n}, but task {n} is "
                    f"{by_num[n].marker}. Either the block was lifted -- record how -- "
                    "or the refusal was ignored.",
                )


def check_skill_names(
    tasks: list[Task], skills: set[str], catalogue: dict[str, str], r: Report
) -> None:
    """Derive each task's owner from its stage; do not trust the Skill field.

    The old rule was that the named skill must exist, and that turned out to do
    active harm. A real 17-task run attributed TEN stages to skills that do not
    own them -- their true owners do not exist in 0.1.0, the linter demanded a
    name that does, so the plan asserted that leakage-guard owns data processing
    and model evaluation. It passed. Seven of the ten apologised in prose the
    linter cannot read, contradicting the field on their own line.

    An attribution has to be checkable against something outside itself, and the
    stage is the only thing that says which skill should own a task. So `Stage:`
    is required, the expected owner comes from the catalogue, and the Skill field
    is verified rather than believed:

      owner exists in skills/  ->  _(Stage: 4 | Skill: leakage-guard)_
      owner does not exist yet ->  _(Stage: 5 | Skill: none; would be: data-processing)_

    That makes the coverage gap a field to count instead of a sentence to hope
    someone reads, and makes a borrowed skill name a failure instead of a pass.
    """
    for t in tasks:
        if not t.stage:
            r.fail(
                "attribution",
                t.line_no,
                f"task {t.num} declares no stage; write _(Stage: <n> | Skill: …)_ so "
                "its owner can be derived from the catalogue rather than taken on "
                "trust. Cross-cutting work names the skill as its stage, e.g. "
                "'Stage: sagemaker-pipeline'.",
            )
            continue

        if t.stage not in catalogue:
            r.fail(
                "attribution",
                t.line_no,
                f"task {t.num} declares stage {t.stage!r}, which is not in the "
                f"catalogue ({', '.join(sorted(catalogue))}).",
            )
            continue

        expected = catalogue[t.stage]
        exists = expected in skills

        if t.unowned:
            for name in t.unowned:
                if not exists and name != expected:
                    r.fail(
                        "attribution",
                        t.line_no,
                        f"task {t.num} is stage {t.stage}, whose would-be owner is "
                        f"{expected!r}, but it names {name!r}.",
                    )
                elif exists:
                    r.fail(
                        "attribution",
                        t.line_no,
                        f"task {t.num} claims no skill owns stage {t.stage}, but "
                        f"{expected!r} exists in this power. Attribute it there.",
                    )
            continue

        if not t.skills:
            r.fail(
                "attribution",
                t.line_no,
                f"task {t.num} names no skill; add _(Skill: {expected})_"
                if exists
                else f"task {t.num} names no skill; stage {t.stage} has no "
                f"implementation, so write _(Skill: none; would be: {expected})_.",
            )
            continue

        for name in t.skills:
            if not exists:
                r.fail(
                    "attribution",
                    t.line_no,
                    f"task {t.num} is stage {t.stage}, which no skill implements in "
                    f"this version, but it is attributed to {name!r}. Borrowing the "
                    "nearest skill records something untrue; write "
                    f"_(Skill: none; would be: {expected})_.",
                )
            elif name != expected:
                r.fail(
                    "attribution",
                    t.line_no,
                    f"task {t.num} is stage {t.stage}, owned by {expected!r}, but it "
                    f"is attributed to {name!r}.",
                )


def load_catalogue(explicit: Path | None) -> dict[str, str]:
    """Map every stage to the skill that owns it: {"5": "data-processing", ...}.

    Read from a file rather than hard-coded, and cross-checked by validate.py
    against skills/ and against the table in SKILL.md, because three lists of the
    same names drift and the drift is silent. Missing is a HARD failure for the
    same reason discover_skills is: attribution is the check that matters here,
    and a linter that skips it while printing OK is worse than one that is absent.
    """
    if explicit is not None:
        path = explicit
    else:
        path = Path(__file__).resolve().parent.parent / "references" / "stage-catalogue.txt"

    if not path.is_file():
        raise SystemExit(
            f"plan-lint: cannot read the stage catalogue at {path}.\n"
            "It maps each stage to the skill that owns it, which is what makes the "
            "attribution check possible. Running the other checks and reporting OK "
            "would misreport the plan as verified."
        )

    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise SystemExit(
                f"plan-lint: malformed catalogue line in {path}: {line!r} "
                "(expected '<stage> <owning-skill>')."
            )
        out[parts[0]] = parts[1]
    if not out:
        raise SystemExit(f"plan-lint: the stage catalogue at {path} is empty.")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Lint an ml-planning PLAN.md.")
    ap.add_argument("plan", type=Path, help="path to PLAN.md")
    ap.add_argument(
        "--skills-dir",
        type=Path,
        default=None,
        help="this power's skills/ directory (default: inferred from this script's location)",
    )
    ap.add_argument(
        "--catalogue",
        type=Path,
        default=None,
        help="stage catalogue file (default: ../references/stage-catalogue.txt)",
    )
    args = ap.parse_args()

    if not args.plan.is_file():
        raise SystemExit(f"plan-lint: no such file: {args.plan}")

    skills = discover_skills(args.skills_dir)
    catalogue = load_catalogue(args.catalogue)
    lines = args.plan.read_text(encoding="utf-8").splitlines(keepends=True)
    header, tasks = parse(lines)

    r = Report()
    check_numbering(tasks, r)
    check_markers(tasks, r)
    check_header(header, tasks, r)
    check_ordering(tasks, r)
    check_skips(tasks, r)
    check_refusals(tasks, r)
    check_skill_names(tasks, skills, catalogue, r)

    if r.ok:
        state_counts: dict[str, int] = {}
        for t in tasks:
            if t.marker:
                state_counts[t.marker] = state_counts.get(t.marker, 0) + 1
        summary = "  ".join(f"{m}×{n}" for m, n in sorted(state_counts.items()))
        unowned = sorted({n for t in tasks for n in t.unowned})
        gap = (
            f"\n    {len(unowned)} stage(s) owned by no skill yet: "
            + ", ".join(unowned)
            if unowned
            else ""
        )
        print(f"OK  {len(tasks)} tasks, {summary}{gap}")
        return 0

    print(f"{len(r.violations)} violation(s) in {args.plan}:", file=sys.stderr)
    for v in r.violations:
        print(f"  {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
