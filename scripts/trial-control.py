#!/usr/bin/env python3
"""Break next.py and report.py one condition at a time, and check the refusal is the
intended one rather than merely some refusal."""
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
LAST_DONE: 2 @ 2026-09-18T10:00:00+08:00

1. [x] **Frame the problem** _(Stage: 1 | Skill: ml-planning)_
2. [x] **Environment readiness** _(Stage: 2 | Skill: ml-planning)_
3. [ ] **Register the dataset** _(Stage: 3 | Skill: data-pipeline)_
4. [ ] **Leakage guard** _(Stage: 4 | Skill: leakage-guard)_
5. [ ] **Data processing** _(Stage: 5 | Skill: data-pipeline)_
"""

TRUNC = GOOD.replace("PRESET: data-prep-only", "PRESET: full-lifecycle")


def run(script, *a):
    p = subprocess.run([sys.executable, str(S / script), *map(str, a)],
                       capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def case(label, want, rc, out, needle=None):
    got = "REFUSED" if rc else "OK"
    ok = got == want and (needle is None or needle in out)
    print(f"  {'✔' if ok else '✘'} {label:<46} {got}")
    if not ok:
        print("      " + out.replace("\n", "\n      ")[:600])
    return ok

fails = 0
plan = D / "PLAN.md"

print("=== next.py 派发 ===")
plan.write_text(GOOD, encoding="utf-8")
rc, out = run("next.py", plan)
fails += not case("干净计划 → 派发", "OK", rc, out, "task")
print("      " + out.replace("\n", "\n      "))

print("\n=== 第十轮的失效形状：preset 声明了却少了尾部阶段 ===")
p2 = D / "trunc.md"; p2.write_text(TRUNC, encoding="utf-8")
rc, out = run("next.py", p2)
fails += not case("缺 6..16 → repair-plan 指令而非违规清单", "OK", rc, out, "repair-plan")
led = (D / "PLAN.state.json")
rc2, out2 = run("next.py", p2)
fails += not case("修计划是引擎要求的，不算篡改", "OK", rc2, out2, "repair-plan")

print("\n=== report.py 按 execution 档拒绝 ===")
plan.write_text(GOOD, encoding="utf-8"); run("next.py", plan)
rc, out = run("report.py", "--plan", plan, "--task", "4", "--state", "S",
              "--reason", "没时间")
fails += not case("[S] 打在 ALWAYS 上", "REFUSED", rc, out, "ALWAYS")

rc, out = run("report.py", "--plan", plan, "--task", "3", "--state", "x")
fails += not case("[x] 但产物不在盘上", "REFUSED", rc, out, "produces")

rc, out = run("report.py", "--plan", plan, "--task", "3", "--state", ">",
              "--execution", "arn:fake")
fails += not case("[>] 打在 inline 阶段上", "REFUSED", rc, out, "mode")

rc, out = run("report.py", "--plan", plan, "--task", "3", "--state", "?")
fails += not case("[?] 缺 --asked", "REFUSED", rc, out, "asked")

print("\n=== 正常记录并前进 ===")
art = D / "artifacts"; art.mkdir(exist_ok=True)
(art / "dataset-manifest.json").write_text("{}", encoding="utf-8")
rc, out = run("report.py", "--plan", plan, "--task", "3", "--state", "x",
              "--artifact", art / "dataset-manifest.json")
fails += not case("[x] 有产物 → 记录", "OK", rc, out, "RECORDED")
rc, out = run("next.py", plan)
fails += not case("下一条指令是 task 4", "OK", rc, out, "task")
print("      " + [l for l in out.splitlines() if "task" in l][0].strip() if not rc else "")

print("\n=== 手改 PLAN.md 之后，改动不算 ===")
# 这次的手改必须是 lint 干净的，否则 lint 先拦下，digest 那条路走不到。
# 把 task 4 标成 [-]（本地进行中）合法：上方无未结项，LAST_DONE 仍是最高的 [x]。
t = plan.read_text(encoding="utf-8").replace(
    "4. [ ] **Leakage guard**", "4. [-] **Leakage guard**")
plan.write_text(t, encoding="utf-8")
rc, out = run("next.py", plan)
fails += not case("lint 干净的手改 → digest 不符被拒", "REFUSED", rc, out, "does not count")
print("      " + out.replace("\n", "\n      ")[:340])
rc2, out2 = run("next.py", plan)
fails += not case("再跑一次仍拒（不会自愈）", "REFUSED", rc2, out2, "digest" if "digest" in out2 else "ledger")


print("\n=== 写坏了会回滚 ===")
plan.write_text(GOOD.replace("LAST_DONE: 2", "LAST_DONE: 2"), encoding="utf-8")
run("next.py", plan)
before = plan.read_text(encoding="utf-8")
rc, out = run("report.py", "--plan", plan, "--task", "5", "--state", "x",
              "--artifact", art / "dataset-manifest.json")
same = plan.read_text(encoding="utf-8") == before
fails += not case("越过前置的 [x] 被拒", "REFUSED", rc, out)
print(f"  {'✔' if same else '✘'} PLAN.md 未被改动（回滚生效）")
fails += not same

print(f"\n{'全部通过' if not fails else str(fails) + ' 项未通过'}")
sys.exit(1 if fails else 0)
