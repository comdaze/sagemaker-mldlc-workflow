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
        ("numbering", "编号跳号",
         sub("5. [ ] **Data processing**", "6. [ ] **Data processing**"),
         "task 5 renumbered to 6, leaving a gap"),
        ("marker", "一个 task 两个标记",
         sub("3. [ ] **Register", "3. [ ] [x] **Register"),
         "two state markers on one task"),
        ("header", "缺 PARTITION",
         sub("PARTITION: aws-cn\n", ""),
         "the partition is undeclared"),
        ("cursor", "LAST_DONE 与最高 [x] 不符",
         sub("3. [ ] **Register", "3. [x] **Register"),
         "a task is [x] but LAST_DONE still says 0"),
        ("ordering", "[x] 压在未结之上",
         sub("5. [ ] **Data processing**", "5. [x] **Data processing**"),
         "the last task is complete while earlier ones are not"),
        ("skip", "[S] 没有理由",
         sub("2. [ ] **Tuning**", "2. [S] **Tuning**", FULL),
         "[S] on a CONDITIONAL stage with no reason"),
        ("skippability", "[S] 打在 ALWAYS 上",
         sub("4. [ ] **Leakage guard**",
             "4. [S] **Leakage guard** skipped: 时间不够"),
         "[S] on a stage declared ALWAYS"),
        ("refusal", "[!] 缺 refused:",
         sub("4. [ ] **Leakage guard**", "4. [!] **Leakage guard**"),
         "a refusal with no artefact and no blocks list"),
        ("awaiting", "[?] 缺 asked:",
         sub("3. [ ] **Register", "3. [?] **Register"),
         "a blocker nobody was told about"),
        ("substitute", "[~] 缺 instead-of:",
         sub("4. [ ] **Leakage guard**", "4. [~] **Leakage guard**"),
         "a substitution that declares neither what nor why"),
        ("attribution", "Skill 与 Stage 不符",
         sub("Stage: 4 | Skill: leakage-guard", "Stage: 4 | Skill: data-pipeline"),
         "the named skill is not the stage's declared owner"),
        ("prerequisite", "前置未满足就完成",
         sub("5. [ ] **Data processing**",
             "5. [-] **Data processing**"),
         "stage 5 started while stage 4 is unsettled"),
        ("preset", "PRESET 覆盖不全",
         sub("4. [ ] **Leakage guard** _(Stage: 4 | Skill: leakage-guard)_\n", ""),
         "the preset includes stage 4 and no task covers it"),
        ("concurrency", "[>] 缺 execution:",
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

    print(f"plan-lint.py: {len(inventory)} 个检查函数，{len(all_labels)} 个违规标签\n")

    print("=== 正例：每个 preset 生成的规范计划都必须通过 ===")
    print("    （这是上一次加规则时从未验过的性质：规则是可满足的）")
    for preset in sorted(doc["presets"]):
        p = D / f"good-{preset}.md"
        p.write_text(generate(preset, doc), encoding="utf-8")
        rc, out = run(p)
        ok = rc == 0
        fails += not ok
        n = len(doc["presets"][preset])
        print(f"  {'✔' if ok else '✘'} {preset:<18} {n:>2} 个 stage   "
              f"{'通过' if ok else '被拒'}")
        if not ok:
            for line in out.strip().splitlines()[:4]:
                print(f"        {line.strip()}")

    print("\n=== 反例：每条检查一个，断言打中的是它自己 ===")
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
        note = f"  （同时触发 {', '.join(extra)}）" if extra else ""
        print(f"  {'✔' if ok else '✘'} {label:<14} {name:<22}{note}")
        if not ok:
            print(f"        断言 [{label}]，实际 {sorted(hit) or '无违规'}")
            print(f"        这个 fixture 破坏的是：{breaks}")

    print("\n=== --artifacts：产物在盘上而 task 说没做 ===")
    art = D / "artifacts"
    art.mkdir()
    (art / "leakage-audit.json").write_text("{}", encoding="utf-8")
    p = D / "neg-workspace.md"
    p.write_text(BASE, encoding="utf-8")
    rc, out = run(p, art)
    ok = rc != 0 and "workspace" in fired(out)
    fails += not ok
    covered.add("workspace")
    print(f"  {'✔' if ok else '✘'} workspace      stage 4 的产物在盘上而 task 4 是 [ ]")
    if not ok:
        print(f"        实际 {sorted(fired(out)) or '无违规'}")

    print("\n=== 自审：有检查没有 fixture 吗 ===")
    missing = sorted(all_labels - covered)
    if missing:
        fails += len(missing)
        for m in missing:
            who = [k for k, v in inventory.items() if m in v]
            print(f"  ✘ [{m}] 没有反例覆盖（来自 {', '.join(who)}）")
        print("    加了检查却不加 fixture，就会退回到「只验过能报错」的状态。")
    else:
        print(f"  ✔ {len(all_labels)} 个标签全部有反例覆盖")

    print(f"\n{'全部通过' if not fails else f'{fails} 项未通过'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
