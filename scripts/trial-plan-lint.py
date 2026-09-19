#!/usr/bin/env python3
"""Regression suite for plan-lint.py. Committed, because the last one was not.

The previous fixtures lived in a scratch directory and are gone, so the eleven checks they
covered have no reproducible evidence behind them. That is the smaller of two problems.

The larger one is what this suite is shaped against. Over one day the linter's rule count
grew from 7 to 11, and **every new rule was verified only to be capable of erroring** --
never that a real, complete, honest plan could still pass all of them. Between the two
trial runs that followed, ML work rose and bookkeeping quality fell, and a linter that
cannot be satisfied is the obvious suspect. So this suite has two halves, and the first
matters more:

  POSITIVE -- a well-formed plan is GENERATED for every preset in stages.toml and must
  pass clean. Adding a rule that no plan can satisfy now breaks the build.

  NEGATIVE -- one fixture per violation label, asserting THAT label fires. A negative that
  trips some other check proves nothing about the rule it was written for; that mistake was
  made in this repository and caught only by re-running an older fixture.

And it audits itself. The label inventory is read from plan-lint.py's own source, so a
check added without a fixture is reported as uncovered and fails the run. A suite that
silently stops covering its subject is worse than no suite, because it still reports
success.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "skills" / "ml-planning" / "scripts"
LINT = SCRIPTS / "plan-lint.py"
STAGES = SCRIPTS.parent / "references" / "stages.toml"
D = Path(os.environ.get("KIROCREW_SCRATCH", "/tmp")) / "plan-lint-trial"


# ------------------------------------------------------------------ inventory


def labels_in_source() -> dict[str, set[str]]:
    """Every violation label plan-lint.py can emit, per check function."""
    src = LINT.read_text(encoding="utf-8")
    found: dict[str, set[str]] = {}
    for blk in re.split(r"\ndef ", src):
        m = re.match(r"(check_\w+)", blk)
        if m:
            found[m.group(1)] = set(re.findall(r'r\.fail\(\s*\n?\s*"([a-z-]+)"', blk))
    return found


def run(plan: Path, artifacts: Path | None = None) -> tuple[int, str]:
    cmd = [sys.executable, str(LINT), str(plan)]
    if artifacts:
        cmd += ["--artifacts", str(artifacts)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def fired(out: str) -> set[str]:
    """Labels that actually prefixed a violation.

    Anchored to the start of the line on purpose: an unanchored search also matched the
    `[x]` and `[-]` inside violation MESSAGES, which made every fixture look as though it
    tripped extra checks. A suite that misreads its own subject's output is not evidence.
    """
    return set(re.findall(r"^\s*\[([a-z-]+)\]", out, re.M))


# ------------------------------------------------------------------ positives


def generate(preset: str, doc: dict) -> str:
    """Build a plan a careful agent would write: every preset stage, correctly attributed.

    Generated from stages.toml rather than hand-written, so it cannot drift from the
    declaration the linter reads. What it proves is not that the generator is clever but
    that the RULES are satisfiable -- the property nobody checked when they were added.
    """
    ids = [str(s) for s in doc["presets"][preset]]
    lines = [
        "# Plan",
        "",
        "PARTITION: aws-cn",
        "SDK: 3.22.0",
        f"PRESET: {preset}",
        "LAST_DONE: none",
        "",
    ]
    for i, sid in enumerate(ids, start=1):
        s = doc["stages"][sid]
        lines.append(
            f"{i}. [ ] **{s['name']}** _(Stage: {sid} | Skill: {s['owner']})_"
        )
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ negatives

BASE = """# Plan

PARTITION: aws-cn
SDK: 3.22.0
PRESET: data-prep-only
LAST_DONE: none

1. [ ] **Frame the problem** _(Stage: 1 | Skill: ml-planning)_
2. [ ] **Environment readiness** _(Stage: 2 | Skill: ml-planning)_
3. [ ] **Register the dataset** _(Stage: 3 | Skill: data-pipeline)_
4. [ ] **Leakage guard** _(Stage: 4 | Skill: leakage-guard)_
5. [ ] **Data processing** _(Stage: 5 | Skill: data-pipeline)_
"""

FULL = """# Plan

PARTITION: aws-cn
SDK: 3.22.0
LAST_DONE: none

1. [ ] **Frame the problem** _(Stage: 1 | Skill: ml-planning)_
2. [ ] **Tuning** _(Stage: 8 | Skill: train-and-tune)_
"""


def negatives() -> list[tuple[str, str, str, str]]:
    """(label, name, plan text, what the fixture breaks)"""
    def sub(old: str, new: str, src: str = BASE) -> str:
        assert old in src, old
        return src.replace(old, new, 1)

    return [
        ("numbering", "a gap in the numbering",
         sub("5. [ ] **Data processing**", "6. [ ] **Data processing**"),
         "task 5 renumbered to 6, leaving a gap"),
        ("marker", "two state markers on one task",
         sub("3. [ ] **Register", "3. [ ] [x] **Register"),
         "two state markers on one task"),
        ("header", "no PARTITION",
         sub("PARTITION: aws-cn\n", ""),
         "the partition is undeclared"),
        ("cursor", "LAST_DONE disagrees with the highest [x]",
         sub("3. [ ] **Register", "3. [x] **Register"),
         "a task is [x] but LAST_DONE still says 0"),
        ("ordering", "[x] above an unsettled task",
         sub("5. [ ] **Data processing**", "5. [x] **Data processing**"),
         "the last task is complete while earlier ones are not"),
        ("skip", "[S] with no reason",
         sub("2. [ ] **Tuning**", "2. [S] **Tuning**", FULL),
         "[S] on a CONDITIONAL stage with no reason"),
        ("skippability", "[S] on an ALWAYS stage",
         sub("4. [ ] **Leakage guard**",
             "4. [S] **Leakage guard** skipped: no time"),
         "[S] on a stage declared ALWAYS"),
        ("refusal", "[!] without refused:",
         sub("4. [ ] **Leakage guard**", "4. [!] **Leakage guard**"),
         "a refusal with no artefact and no blocks list"),
        ("awaiting", "[?] without asked:",
         sub("3. [ ] **Register", "3. [?] **Register"),
         "a blocker nobody was told about"),
        ("substitute", "[~] without instead-of:",
         sub("4. [ ] **Leakage guard**", "4. [~] **Leakage guard**"),
         "a substitution that declares neither what nor why"),
        ("attribution", "Skill: does not match the owner Stage: implies",
         sub("Stage: 4 | Skill: leakage-guard", "Stage: 4 | Skill: data-pipeline"),
         "the named skill is not the stage's declared owner"),
        ("prerequisite", "progress with a prerequisite unmet",
         sub("5. [ ] **Data processing**",
             "5. [-] **Data processing**"),
         "stage 5 started while stage 4 is unsettled"),
        ("preset", "PRESET names stages no task covers",
         sub("4. [ ] **Leakage guard** _(Stage: 4 | Skill: leakage-guard)_\n", ""),
         "the preset includes stage 4 and no task covers it"),
        ("preset", "no PRESET line at all",
         sub("PRESET: data-prep-only\n", ""),
         "no PRESET at all -- a run that describes its scope in prose instead leaves three "
         "checks with nothing to compare the tasks against"),
        ("concurrency", "[>] without execution:",
         sub("5. [ ] **Data processing**", "5. [>] **Data processing**"),
         "[>] without the run it is awaiting"),
    ]


def main() -> int:
    shutil.rmtree(D, ignore_errors=True)
    D.mkdir(parents=True)
    doc = tomllib.loads(STAGES.read_text(encoding="utf-8"))
    inventory = labels_in_source()
    all_labels = set().union(*inventory.values()) if inventory else set()
    fails = 0

    print(f"plan-lint.py: {len(inventory)} check functions, {len(all_labels)} violation labels\n")

    print("=== positives: a generated plan for every preset must pass ===")
    print("    (the property nobody checked when the rules were added: they are satisfiable)")
    for preset in sorted(doc["presets"]):
        p = D / f"good-{preset}.md"
        p.write_text(generate(preset, doc), encoding="utf-8")
        rc, out = run(p)
        ok = rc == 0
        fails += not ok
        n = len(doc["presets"][preset])
        print(f"  {'PASS' if ok else 'FAIL'} {preset:<18} {n:>2} stages")
        if not ok:
            for line in out.strip().splitlines()[:4]:
                print(f"        {line.strip()}")

    print("\n=== negatives: one per check, asserting THAT check fires ===")
    covered: set[str] = set()
    for label, name, text, breaks in negatives():
        p = D / f"neg-{label}.md"
        p.write_text(text, encoding="utf-8")
        rc, out = run(p)
        hit = fired(out)
        ok = rc != 0 and label in hit
        fails += not ok
        covered.add(label)
        extra = sorted(hit - {label})
        note = f"  (also fired: {', '.join(extra)})" if extra else ""
        print(f"  {'PASS' if ok else 'FAIL'} {label:<14} {name:<44}{note}")
        if not ok:
            print(f"        expected [{label}], got {sorted(hit) or 'no violations'}")
            print(f"        this fixture breaks: {breaks}")

    print("\n=== --artifacts: an artefact exists while its task says otherwise ===")
    art = D / "artifacts"
    art.mkdir()
    (art / "leakage-audit.json").write_text("{}", encoding="utf-8")
    p = D / "neg-workspace.md"
    p.write_text(BASE, encoding="utf-8")
    rc, out = run(p, art)
    ok = rc != 0 and "workspace" in fired(out)
    fails += not ok
    covered.add("workspace")
    print(f"  {'PASS' if ok else 'FAIL'} workspace      stage 4 artefact exists, task 4 is [ ]")
    if not ok:
        print(f"        got {sorted(fired(out)) or 'no violations'}")

    print("\n=== self-audit: is any check left uncovered? ===")
    missing = sorted(all_labels - covered)
    if missing:
        fails += len(missing)
        for m in missing:
            who = [k for k, v in inventory.items() if m in v]
            print(f"  FAIL [{m}] has no negative fixture (emitted by {', '.join(who)})")
        print("    A check added without one returns us to rules verified only to be able to error.")
    else:
        print(f"  PASS all {len(all_labels)} labels have a negative fixture")

    print(f"\n{'all passed' if not fails else f'{fails} failed'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
