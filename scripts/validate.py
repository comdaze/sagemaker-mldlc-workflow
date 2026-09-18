#!/usr/bin/env python3
"""Validate this power against the Agent Plugins 1.0.0 specification.

Two layers of checking, because they catch different classes of mistake:

  1. Schema validation of plugin.json and mcp.json against the live schemas at
     agent-plugins.org. These schemas declare additionalProperties: false, so a
     stray Kiro-specific or Codex-specific key is a hard failure -- which is
     exactly how the upstream .mcp.json fails (no $schema, no type, has
     "disabled"). Schemas are cached under schemas/ so this runs offline; pass
     --refresh to re-fetch.

  2. The structural checklist from the official power-builder `migrate-plugin`
     skill, which the schemas do not cover: every skill needs a SKILL.md whose
     frontmatter `name` matches its directory name, and a description.

Exit code 0 = ready to install and submit. Non-zero = the list of violations.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schemas"

SCHEMAS = {
    "plugin": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    "mcp": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
}

# Kiro-specific keys the migrate-plugin skill says to strip from mcp.json.
NON_SPEC_MCP_KEYS = ("disabled", "autoApprove", "disabledTools")

errors: list[str] = []
warnings: list[str] = []
skipped: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def fetch(url: str) -> str:
    """Fetch over HTTPS, falling back to curl.

    Some Python installs have no usable CA bundle and raise
    CERTIFICATE_VERIFY_FAILED where curl succeeds. Without the fallback a
    --refresh run looks like a network outage on a machine that is online.
    """
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except Exception:
        out = subprocess.run(
            ["curl", "-sSf", "-m", "30", "-L", url],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout


def load_schema(kind: str, refresh: bool) -> dict:
    cached = SCHEMA_DIR / f"{kind}.schema.json"
    if refresh or not cached.exists():
        body = fetch(SCHEMAS[kind])
        SCHEMA_DIR.mkdir(exist_ok=True)
        cached.write_text(body, encoding="utf-8")
    return json.loads(cached.read_text(encoding="utf-8"))


def check_against_schema(kind: str, path: Path, refresh: bool) -> None:
    """Schema-validate one manifest.

    A validator that cannot run its main check must NOT report success: an
    unavailable schema or a missing jsonschema is a hard failure, not a warning.
    Pass --allow-skip-schema to downgrade it, and then the summary line says so.
    """
    try:
        import jsonschema
    except ImportError:
        skipped.append(f"{path.name}: jsonschema is not installed")
        return
    try:
        schema = load_schema(kind, refresh)
    except Exception as exc:
        skipped.append(f"{path.name}: could not load the {kind} schema ({exc})")
        return
    doc = json.loads(path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "(root)"
        fail(f"{path.name}: {loc}: {err.message}")


def check_plugin_name(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    name = doc.get("name", "")
    # Same pattern the schema enforces, restated so the failure message is useful.
    if not re.fullmatch(r"(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", name):
        fail(f"plugin.json: name {name!r} violates the kebab-case naming constraint")


def check_mcp_extras(path: Path) -> None:
    if not path.exists():
        return
    doc = json.loads(path.read_text(encoding="utf-8"))
    for server_name, server in (doc.get("mcpServers") or {}).items():
        for key in NON_SPEC_MCP_KEYS:
            if key in server:
                fail(f"mcp.json: server {server_name!r} still carries Kiro-only key {key!r}")
        if "type" not in server:
            fail(f"mcp.json: server {server_name!r} is missing the required 'type' field")


def check_stage_catalogue() -> None:
    """The stage catalogue, skills/ and SKILL.md's table must name the same stages.

    plan-lint.py derives each task's expected owner, its skippability class, its
    prerequisites and its execution mode from this one file, so a name that drifts out of
    step with skills/ turns the attribution check from a refusal into a wrong answer.

    It replaced two flat lists that had already drifted from each other. One declaration
    cannot disagree with itself; three copies of the same names will.
    """
    cat = ROOT / "skills" / "ml-planning" / "references" / "stages.toml"
    if not cat.exists():
        fail("skills/ml-planning/references/stages.toml is missing -- plan-lint.py needs it")
        return

    try:
        import tomllib
    except ImportError:
        warn("tomllib unavailable, so the stage catalogue is unchecked (needs Python 3.11+)")
        return
    try:
        doc = tomllib.loads(cat.read_text(encoding="utf-8"))
    except Exception as e:
        fail(f"stages.toml is not valid TOML: {e}")
        return

    stages = doc.get("stages") or {}
    if not stages:
        fail("stages.toml declares no stages")
        return

    owners = {s.get("owner") for s in stages.values() if isinstance(s, dict)}
    on_disk = {d.name for d in (ROOT / "skills").iterdir() if (d / "SKILL.md").is_file()}
    for name in sorted(on_disk - owners):
        fail(
            f"skill {name!r} exists in skills/ but owns no stage in stages.toml -- "
            "plan-lint would reject a task attributed to it"
        )
    for name in sorted(owners - on_disk):
        if name:
            warn(f"stages.toml names owner {name!r}, which is not a skill in skills/")

    # Every stage must declare the fields the rules read. Omitting one used to be silent:
    # report.py defaulted a missing `execution` to CONDITIONAL, the most permissive class, so
    # an incomplete declaration quietly became a skippable stage. plan-lint indexed the same
    # field directly and would have crashed instead. Neither is an answer, so the declaration
    # is checked here where it is written.
    CLASSES = {"ALWAYS", "DELIVERABLE", "CONDITIONAL"}
    MODES = {"inline", "pipeline"}
    for sid, s in stages.items():
        if s.get("execution") not in CLASSES:
            fail(f"stage {sid} declares execution {s.get('execution')!r}; expected one of "
                 f"{sorted(CLASSES)}. An undeclared class has no rule and must not fall back "
                 "to the most permissive one.")
        if s.get("mode") not in MODES:
            fail(f"stage {sid} declares mode {s.get('mode')!r}; expected one of "
                 f"{sorted(MODES)}. `[>]` is legal only on a pipeline-mode stage, so an "
                 "undeclared mode makes that rule unenforceable.")
        if not s.get("name") or not s.get("owner"):
            fail(f"stage {sid} is missing a name or an owner.")

    # Every stage a preset names must exist. A preset MAY exclude an ALWAYS stage --
    # `data-prep-only` legitimately stops before modelling. ALWAYS constrains what may
    # be skipped once a preset includes it, which is plan-lint's job, not this one.
    for pname, ids in (doc.get("presets") or {}).items():
        for sid in ids:
            if str(sid) not in stages:
                fail(f"preset {pname!r} names stage {sid}, which stages.toml does not declare")

    # A prerequisite must itself be declared, and a preset that includes a stage should
    # include what that stage requires -- otherwise the plan cannot satisfy it.
    for sid, s in stages.items():
        for req in s.get("requires", []) or []:
            if str(req) not in stages:
                fail(f"stage {sid} requires stage {req}, which stages.toml does not declare")
    ext_all = doc.get("presets-satisfied-externally") or {}
    for pname, ids in (doc.get("presets") or {}).items():
        included = {str(s) for s in ids}
        external = {str(s) for s in (ext_all.get(pname) or [])}
        for sid in included:
            for req in (stages.get(sid, {}).get("requires") or []):
                if str(req) not in included and str(req) not in external:
                    warn(
                        f"preset {pname!r} includes stage {sid} but neither includes its "
                        f"prerequisite {req} nor lists it under "
                        "presets-satisfied-externally; a plan on this preset cannot "
                        "satisfy it"
                    )
    for pname, ids in ext_all.items():
        if pname not in (doc.get("presets") or {}):
            fail(f"presets-satisfied-externally names {pname!r}, which is not a preset")
        for sid in ids:
            if str(sid) not in stages:
                fail(f"presets-satisfied-externally[{pname}] names undeclared stage {sid}")

    skill_md = ROOT / "skills" / "ml-planning" / "SKILL.md"
    if skill_md.exists():
        body = skill_md.read_text(encoding="utf-8")
        for name in sorted(o for o in owners if o):
            if f"`{name}`" not in body:
                warn(
                    f"stages.toml names {name!r} but ml-planning's stage table does not "
                    "mention it; the reader and the linter disagree"
                )


def check_import_cleanliness() -> None:
    """Nothing outside version control is sitting in the tree waiting to be packaged.

    Kiro's "Import power from a folder" copies the WORKING DIRECTORY, not the git tree.
    `.gitignore` therefore protects the repository and not the artefact: a file ignored
    because it is machine-local gets packaged into the installed power anyway.

    This is not hypothetical. `.kiro/settings/cli.json` -- one machine's editor settings,
    ignored on purpose -- was packaged into an installed copy of this power, twice. It was
    caught the second time only because someone happened to look.

    So the rule that would otherwise be "remember to run git status before importing" is
    this check instead, in the script you already run before importing. An IGNORED file
    fails: it is ignored precisely because it should not ship. An UNTRACKED file warns,
    because work in progress is normal and only the author knows whether it belongs.
    """
    if not (ROOT / ".git").exists():
        warn("not a git repository, so import cleanliness cannot be checked")
        return

    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--ignored"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as e:
        warn(f"could not run git status, so import cleanliness is unchecked: {e}")
        return

    if out.returncode != 0:
        warn(f"git status failed ({out.returncode}), so import cleanliness is unchecked")
        return

    ignored, untracked = [], []
    for line in out.stdout.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip()
        if code == "!!":
            ignored.append(path)
        elif code == "??":
            untracked.append(path)

    for p in ignored:
        fail(
            f"{p} is ignored by git but present in the tree. 'Import power from a folder' "
            "copies the working directory, so an ignored file ships anyway -- and it is "
            "ignored because it should not. Remove it before importing."
        )
    for p in untracked:
        warn(
            f"{p} is untracked and would be packaged by a folder import. Commit it or "
            "remove it, so the installed power matches the repository."
        )


def check_power_md(path: Path) -> bool:
    """Cross-check the legacy POWER.md manifest against plugin.json.

    Both manifests may be present, and Kiro renders the power page from POWER.md
    while loading skills from the plugin format. That split is useful and it is
    also a drift hazard: the same power can advertise two different names, two
    descriptions and two authors, and nothing would notice.

    Measured on 2026-09-16 by importing a repository carrying both: the page
    title, the `by` line and the keyword tags all came from POWER.md -- the tags
    were rendered as `machine learning` (POWER.md) rather than
    `machine-learning` (plugin.json), which is what settles it -- while the five
    skills still loaded from skills/. So POWER.md decides what a user sees.

    Returns True when POWER.md exists, so the caller can say so.
    """
    if not path.exists():
        return False

    front = parse_frontmatter(path.read_text(encoding="utf-8"))
    if front is None:
        fail("POWER.md: no YAML frontmatter -- Kiro reads the manifest from there")
        return True

    for field in ("name", "displayName", "description", "keywords", "author"):
        if field not in front:
            fail(f"POWER.md: frontmatter is missing {field!r}")

    # The legacy reader wants a plain string here. An Agent Plugins-shaped
    # object validates against that schema and renders as an empty `by` line,
    # which looked like a Kiro bug until the two shapes were compared.
    author = front.get("author")
    if author is not None and not isinstance(author, str):
        fail(
            f"POWER.md: author must be a string, not {type(author).__name__} -- "
            "the object form is plugin.json's shape and renders blank"
        )

    plugin = ROOT / "plugin.json"
    if not plugin.exists():
        return True
    doc = json.loads(plugin.read_text(encoding="utf-8"))

    if front.get("name") != doc.get("name"):
        fail(
            f"POWER.md name {front.get('name')!r} != plugin.json name "
            f"{doc.get('name')!r} -- one power, one identity"
        )

    p_author = doc.get("author")
    p_author_name = p_author.get("name") if isinstance(p_author, dict) else p_author
    if isinstance(author, str) and p_author_name and author != p_author_name:
        fail(
            f"POWER.md author {author!r} != plugin.json author {p_author_name!r} -- "
            "the page shows POWER.md's, so they must agree"
        )

    # Descriptions are prose and will not be identical -- POWER.md's carries a
    # Kiro-specific SETUP prefix because the power page has no install section
    # and its truncated subtitle is the most prominent text on the page.
    # Requiring identical openings would forbid that prefix; requiring that
    # plugin.json's opening appear SOMEWHERE still catches the real hazard,
    # which is one description being rewritten and the other left behind.
    a = (front.get("description") or "").strip()
    b = (doc.get("description") or "").strip()
    if a and b and b[:60] not in a:
        warn(
            "plugin.json's description opening does not appear in POWER.md's -- "
            "one of the two was probably rewritten alone (the page shows POWER.md's)"
        )

    steering = ROOT / "steering"
    if steering.exists():
        for f in sorted(steering.glob("*.md")):
            if parse_frontmatter(f.read_text(encoding="utf-8")) is not None:
                warn(
                    f"steering/{f.name} carries frontmatter; the official powers' "
                    "steering files are plain markdown"
                )
    return True


def parse_frontmatter(text: str) -> dict | None:
    match = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    if not match:
        return None
    try:
        import yaml

        return yaml.safe_load(match.group(1)) or {}
    except ImportError:
        out: dict = {}
        for line in match.group(1).splitlines():
            m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip("\"'")
        return out


def read_manifest() -> list[str]:
    """Names of the upstream-owned skill directories.

    Returns an empty list when the manifest is absent, which the caller reports
    rather than treating every skill as repo-owned.
    """
    path = ROOT / "upstream-skills.txt"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def check_skills() -> tuple[int, int]:
    skills_dir = ROOT / "skills"
    if not skills_dir.is_dir():
        fail("skills/ directory is missing")
        return 0, 0
    dirs = sorted(d for d in skills_dir.iterdir() if d.is_dir())
    if not dirs:
        fail("skills/ contains no skill directories")

    manifest = read_manifest()
    present = {d.name for d in dirs}
    # The manifest is OPTIONAL by design, so this one validator serves both a
    # power that mirrors an upstream plugin (where it says which directories a
    # sync may overwrite) and a power whose skills are all original (where there
    # is nothing to mirror and every skill is repo-owned).
    for name in manifest:
        if name not in present:
            fail(
                f"upstream-skills.txt lists {name!r} but skills/{name} does not exist "
                f"-- run scripts/sync-upstream.sh"
            )
    repo_owned = sorted(present - set(manifest))

    for d in dirs:
        skill_md = d / "SKILL.md"
        if not skill_md.exists():
            fail(f"skills/{d.name}: no SKILL.md")
            continue
        fm = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        if fm is None:
            fail(f"skills/{d.name}/SKILL.md: no YAML frontmatter")
            continue
        name = str(fm.get("name", "")).strip()
        if not name:
            fail(f"skills/{d.name}/SKILL.md: frontmatter has no 'name'")
        elif name != d.name:
            fail(
                f"skills/{d.name}/SKILL.md: frontmatter name {name!r} "
                f"does not match its directory name"
            )
        if not str(fm.get("description", "")).strip():
            fail(f"skills/{d.name}/SKILL.md: frontmatter has no 'description'")

    if repo_owned:
        label = (
            "repo-owned skills (never overwritten by a sync)"
            if manifest
            else "skills (all original to this repository; no upstream mirror)"
        )
        print(f"INFO  {label}: {', '.join(repo_owned)}")
    return len(manifest), len(repo_owned)


def check_regressions(skip: bool) -> None:
    """Run the two committed suites, so a broken refusal fails validation.

    Every script in this power exists because a rule that stays prose holds only while
    someone remembers it. The same is true one level up: a suite nobody runs is prose about
    the rules. The last set of plan-lint fixtures lived in a scratch directory and vanished,
    which is precisely how the linter reached eleven rules with none of them checked against
    a plan that ought to pass.
    """
    suites = [
        ("scripts/trial-plan-lint.py", []),
        ("scripts/trial-control.py", ["skills/ml-planning/scripts"]),
        ("scripts/trial-fixes.py", []),
    ]
    if skip:
        for name, _ in suites:
            warnings.append(f"{name} was not run (--no-regressions)")
        return
    for name, extra in suites:
        path = ROOT / name
        if not path.is_file():
            fail(f"{name} is missing -- the refusals it covers have no evidence behind them")
            continue
        proc = subprocess.run(
            [sys.executable, str(path), *extra],
            capture_output=True, text=True, cwd=ROOT,
        )
        if proc.returncode != 0:
            tail = [l for l in (proc.stdout + proc.stderr).splitlines() if "✘" in l][:6]
            detail = ("\n        " + "\n        ".join(tail)) if tail else ""
            fail(f"{name} reports failures, so a refusal this power advertises is not "
                 f"holding.{detail}")

    # Running the suites makes Python cache the modules they import, and this validator
    # fails on a git-ignored file in the tree. Clean up after ourselves rather than
    # reporting a fault this check created.
    for cache in ROOT.rglob("__pycache__"):
        if ".git" not in cache.parts:
            shutil.rmtree(cache, ignore_errors=True)


def check_docs_track_code() -> None:
    """Every preset and every user-signature field must be named where a reader will find it.

    This power's own taxonomy lists "this README staying in step with the skills" as ADVICE --
    the tier that holds only while someone remembers. Nobody did: five mechanisms reached the
    scripts and the skill bodies while both README.md and docs/DESIGN.md still described the
    version before them, and `realtime-serving` existed in stages.toml alone, so a preset added
    precisely because a legitimate scope had no name went on having no name a user could see.

    Drift was silent because nothing compared the two. It is not advice any more.
    """
    toml_path = ROOT / "skills" / "ml-planning" / "references" / "stages.toml"
    if not toml_path.is_file():
        return
    try:
        import tomllib
        doc = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a malformed file is reported by the other check
        warn(f"stages.toml could not be parsed for the docs cross-check ({exc})")
        return

    body = (ROOT / "skills" / "ml-planning" / "SKILL.md")
    body_text = body.read_text(encoding="utf-8") if body.is_file() else ""
    readme = (ROOT / "README.md")
    readme_text = readme.read_text(encoding="utf-8") if readme.is_file() else ""

    for name in (doc.get("presets") or {}):
        if name not in body_text:
            fail(f"preset {name!r} is declared in stages.toml and named nowhere in "
                 "ml-planning/SKILL.md. A scope a user cannot discover is a scope they will "
                 "describe in prose instead, which is what silences the checks that read PRESET.")
        if name not in readme_text:
            warn(f"preset {name!r} is not named in README.md")

    # A field that carries the user's signature is the whole point of the rule requiring it, so
    # it has to be findable outside the source.
    for field in ("methodChosenBy", "strategyChosenBy", "computeChosenBy", "waived-by",
                  "APPROVED", "useSpot"):
        where = [n for n, t in (("README.md", readme_text),
                                ("skills/*/SKILL.md", "".join(
                                    p.read_text(encoding="utf-8")
                                    for p in sorted((ROOT / "skills").glob("*/SKILL.md")))))
                 if field in t]
        if not where:
            fail(f"{field} is required by a script and appears in neither README.md nor any "
                 "SKILL.md. A rule nobody can read is a rule that will be met by accident or "
                 "not at all.")


def check_no_escaping_paths() -> None:

    """The checklist forbids referencing paths outside the plugin root."""
    for path in ROOT.rglob("*"):
        if path.is_symlink():
            target = (path.parent / Path(path).readlink()).resolve()
            if not str(target).startswith(str(ROOT)):
                fail(f"{path.relative_to(ROOT)}: symlink points outside the plugin root")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh", action="store_true", help="re-fetch the schemas instead of using schemas/"
    )
    parser.add_argument(
        "--allow-skip-schema",
        action="store_true",
        help="do not fail when a schema cannot be loaded (offline, no jsonschema)",
    )
    parser.add_argument(
        "--no-regressions",
        action="store_true",
        help="skip the committed fixture suites (they are the evidence; skipping is a WARN)",
    )
    args = parser.parse_args()

    plugin_json = ROOT / "plugin.json"
    mcp_json = ROOT / "mcp.json"

    if not plugin_json.exists():
        fail("plugin.json is missing -- it is the one required file")
    else:
        check_against_schema("plugin", plugin_json, args.refresh)
        check_plugin_name(plugin_json)

    if mcp_json.exists():
        check_against_schema("mcp", mcp_json, args.refresh)
        check_mcp_extras(mcp_json)

    upstream_count, owned_count = check_skills()
    check_no_escaping_paths()
    has_power_md = check_power_md(ROOT / "POWER.md")
    check_stage_catalogue()
    check_docs_track_code()
    check_regressions(args.no_regressions)
    check_import_cleanliness()

    for w in warnings:
        print(f"WARN  {w}")
    for s in skipped:
        label = "WARN " if args.allow_skip_schema else "FAIL "
        print(f"{label} schema validation skipped -- {s}")
    if skipped and not args.allow_skip_schema:
        errors.extend(f"schema validation skipped -- {s}" for s in skipped)

    if errors:
        for e in errors:
            if not e.startswith("schema validation skipped"):
                print(f"FAIL  {e}")
        print(f"\n{len(errors)} violation(s).")
        return 1

    suffix = " (schema checks skipped)" if skipped else ""
    manifests = "plugin.json, mcp.json," if mcp_json.exists() else "plugin.json and"
    ownership = (
        f"({upstream_count} upstream + {owned_count} repo-owned) "
        if upstream_count
        else ""
    )
    print(
        f"OK  {manifests} {upstream_count + owned_count} skills "
        f"{ownership}conform to Agent Plugins 1.0.0.{suffix}"
    )
    if has_power_md:
        print("OK  POWER.md agrees with plugin.json (Kiro renders the page from POWER.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
