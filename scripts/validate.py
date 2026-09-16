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
    return 0


if __name__ == "__main__":
    sys.exit(main())
