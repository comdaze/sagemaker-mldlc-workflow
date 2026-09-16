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

# The six states. Anything else in a task's marker position is a violation
# rather than something to interpret generously.
STATES = {
    "[ ]": "not started",
    "[-]": "in progress",
    "[?]": "awaiting a human decision",
    "[R]": "revising after a failed gate or review",
    "[x]": "done",
    "[S]": "skipped",
}

# A task that will never be worked on again. Ordering is checked against these
# two together, because a skipped task must not block the ones after it.
TERMINAL = {"[x]", "[S]"}

PARTITIONS = {"aws", "aws-cn", "aws-us-gov"}

TASK_RE = re.compile(r"^(?P<num>\d+)\.\s+(?P<rest>.*)$")
MARKER_RE = re.compile(r"\[(?: |-|\?|R|x|S)\]")
SKILL_RE = re.compile(r"_\(Skill:\s*(?P<name>[A-Za-z0-9._-]+)\s*\)_")
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
        self.skills = SKILL_RE.findall(text)

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


def check_skill_names(tasks: list[Task], skills: set[str], r: Report) -> None:
    """Every task must be attributed to a skill that exists in this power.

    This is the mechanical form of the planner's own rule that it must not plan
    capabilities no skill covers.
    """
    for t in tasks:
        if not t.skills:
            r.fail(
                "attribution",
                t.line_no,
                f"task {t.num} names no skill; add _(Skill: <name>)_.",
            )
            continue
        for name in t.skills:
            if name not in skills:
                r.fail(
                    "attribution",
                    t.line_no,
                    f"task {t.num} is attributed to {name!r}, which is not a skill in "
                    f"this power ({', '.join(sorted(skills))}). The plan promises "
                    "something nothing here implements.",
                )


def main() -> int:
    ap = argparse.ArgumentParser(description="Lint an ml-planning PLAN.md.")
    ap.add_argument("plan", type=Path, help="path to PLAN.md")
    ap.add_argument(
        "--skills-dir",
        type=Path,
        default=None,
        help="this power's skills/ directory (default: inferred from this script's location)",
    )
    args = ap.parse_args()

    if not args.plan.is_file():
        raise SystemExit(f"plan-lint: no such file: {args.plan}")

    skills = discover_skills(args.skills_dir)
    lines = args.plan.read_text(encoding="utf-8").splitlines(keepends=True)
    header, tasks = parse(lines)

    r = Report()
    check_numbering(tasks, r)
    check_markers(tasks, r)
    check_header(header, tasks, r)
    check_ordering(tasks, r)
    check_skips(tasks, r)
    check_skill_names(tasks, skills, r)

    if r.ok:
        state_counts: dict[str, int] = {}
        for t in tasks:
            if t.marker:
                state_counts[t.marker] = state_counts.get(t.marker, 0) + 1
        summary = "  ".join(f"{m}×{n}" for m, n in sorted(state_counts.items()))
        print(f"OK  {len(tasks)} tasks, {summary}")
        return 0

    print(f"{len(r.violations)} violation(s) in {args.plan}:", file=sys.stderr)
    for v in r.violations:
        print(f"  {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
