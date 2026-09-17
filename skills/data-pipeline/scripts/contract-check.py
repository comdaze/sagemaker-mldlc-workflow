#!/usr/bin/env python3
"""Check a dataset manifest and a processing report against the data-pipeline contract.

Every check here corresponds to a refusal in SKILL.md. The point of putting them in a
script rather than in prose is that a script exits non-zero: a rule that can refuse
holds, and a rule that stays prose holds only while someone remembers it.

Offline by design. It reads what the pipeline recorded rather than calling AWS, so it
runs in CI, runs without credentials, and cannot pass because a network call was skipped.
The one thing it cannot check is whether the recorded values are true; that is what the
read-back-after-upload step in SKILL.md is for, and this asserts that step's result was
written down.

Usage:
    contract-check.py <dataset-manifest.json> <processing-report.json>
    contract-check.py <dataset-manifest.json>          # manifest checks only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# A version id that is absent, null, or the STRING "null" all mean the same thing:
# the object has no immutable identity. The string form is the one that slips
# through, because it is truthy and serialises without complaint.
NULLISH = {None, "", "null", "None", "nil", "undefined"}

REQUIRED_MANIFEST = ("uri", "versionId", "etag", "digest", "sampleCount")

DIGEST_RE = re.compile(r"^(sha256:)?[0-9a-f]{64}$", re.I)


class Report:
    """Collects every violation so one run reports all of them."""

    def __init__(self) -> None:
        self.violations: list[str] = []
        self.notes: list[str] = []

    def fail(self, check: str, message: str) -> None:
        self.violations.append(f"[{check}] {message}")

    def note(self, message: str) -> None:
        self.notes.append(message)

    @property
    def ok(self) -> bool:
        return not self.violations


def load(path: Path, r: Report) -> dict | None:
    if not path.is_file():
        r.fail("input", f"no such file: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        r.fail("input", f"{path} is not valid JSON: {e}")
        return None


def check_manifest(m: dict, r: Report) -> None:
    """Stage 3: the dataset has an identity, and the identity has no holes."""
    for field in REQUIRED_MANIFEST:
        if field not in m:
            r.fail(
                "identity",
                f"manifest is missing {field!r}. Without it the dataset has a "
                "location but not an identity, and a location can change under a "
                "training job without anything noticing.",
            )

    vid = m.get("versionId")
    if field_present(m, "versionId") and vid in NULLISH:
        r.fail(
            "identity",
            f"versionId is {vid!r}. A bucket without versioning cannot issue one, and "
            "it cannot be assigned retroactively -- enable versioning and re-upload. "
            'The string "null" is the form that slips through, because it is truthy.',
        )

    digest = m.get("digest")
    if field_present(m, "digest") and digest not in NULLISH:
        if not DIGEST_RE.match(str(digest)):
            r.fail(
                "identity",
                f"digest {digest!r} is not a sha256 hex digest. A fingerprint nobody "
                "can recompute the same way is not a fingerprint.",
            )
        if str(digest).lower() == str(m.get("etag", "")).lower():
            r.fail(
                "identity",
                "digest equals etag. They are different fingerprints computed by "
                "different parties -- an etag's algorithm changes with multipart "
                "thresholds -- so recording one twice loses the local check.",
            )

    n = m.get("sampleCount")
    if field_present(m, "sampleCount") and (not isinstance(n, int) or n <= 0):
        r.fail("identity", f"sampleCount is {n!r}; expected a positive integer.")

    if not m.get("readBackVerified"):
        r.fail(
            "identity",
            "manifest does not record readBackVerified. Registration must re-read the "
            "remote identity and compare it with what was sent; the comparison is one "
            "call and it catches the whole class of half-finished upload.",
        )


def field_present(m: dict, field: str) -> bool:
    return field in m


def check_completeness(rep: dict, r: Report) -> None:
    """Stage 5: the completeness assertion exists, and its verdict is recorded."""
    c = rep.get("completeness")
    if not isinstance(c, dict):
        r.fail(
            "completeness",
            "processing report records no completeness assertion. Incomplete data does "
            "not fail -- it produces a file that opens cleanly and trains without "
            "error. An absent assertion is worse than a failed one.",
        )
        return

    for field in ("unit", "expectedPerUnit", "passed"):
        if field not in c:
            r.fail("completeness", f"completeness is missing {field!r}.")

    if c.get("passed") is False:
        r.note(
            "completeness.passed is false -- recorded honestly, and the processed "
            "outputs must not be consumed until it is resolved."
        )


def check_counts(rep: dict, manifest: dict | None, r: Report) -> None:
    """Every input sample is accounted for: kept, or dropped on the record."""
    parts = rep.get("partitions")
    if not isinstance(parts, dict) or not parts:
        r.fail("counts", "processing report records no partitions.")
        return

    counts = {}
    for name, p in parts.items():
        if not isinstance(p, dict) or "sampleCount" not in p:
            r.fail("counts", f"partition {name!r} records no sampleCount.")
            continue
        counts[name] = p["sampleCount"]

    if manifest is None or "sampleCount" not in manifest:
        return
    total_in = manifest["sampleCount"]
    dropped = rep.get("filteredOutCount")
    if dropped is None:
        r.fail(
            "counts",
            "processing report records no filteredOutCount. Without it a filter that "
            "dropped part of the data looks like a smaller dataset rather than a "
            "decision nobody wrote down.",
        )
        return

    got = sum(counts.values()) + dropped
    if got != total_in:
        r.fail(
            "counts",
            f"partitions ({sum(counts.values())}) plus filteredOut ({dropped}) is "
            f"{got}, but the manifest registered {total_in} samples. "
            f"{abs(total_in - got)} unaccounted for.",
        )


def check_groups(rep: dict, r: Report) -> None:
    """A grouped split means no group identity appears in two partitions."""
    parts = rep.get("partitions") or {}
    seen: dict[str, str] = {}
    for name, p in parts.items():
        if not isinstance(p, dict):
            continue
        for g in p.get("groupKeys") or []:
            if g in seen and seen[g] != name:
                r.fail(
                    "groups",
                    f"group {g!r} appears in both {seen[g]!r} and {name!r}. A grouped "
                    "split exists to stop exactly this.",
                )
            seen[g] = name


def check_boundaries(rep: dict, r: Report) -> None:
    """Chronological partitions do not overlap, and are ordered."""
    parts = rep.get("partitions") or {}
    spans = []
    for name, p in parts.items():
        if not isinstance(p, dict):
            continue
        lo, hi = p.get("from"), p.get("to")
        if lo is not None and hi is not None:
            if str(lo) > str(hi):
                r.fail("boundaries", f"partition {name!r} has from > to ({lo} > {hi}).")
            spans.append((str(lo), str(hi), name))

    spans.sort()
    for (lo1, hi1, n1), (lo2, hi2, n2) in zip(spans, spans[1:]):
        if lo2 <= hi1:
            r.fail(
                "boundaries",
                f"{n1!r} ends at {hi1} and {n2!r} starts at {lo2} -- they overlap. A "
                "boundary is drawn between prediction units, never inside one.",
            )


def check_fitted(rep: dict, r: Report) -> None:
    """Every fitted artefact records what it was fitted on."""
    arts = rep.get("fittedArtefacts")
    if arts is None:
        r.fail(
            "provenance",
            "processing report records no fittedArtefacts. An empty list is a legal "
            "answer -- nothing was fitted -- but the field being absent means nobody "
            "checked, and a transform fitted above the split is the classic leak.",
        )
        return
    for a in arts:
        if not isinstance(a, dict) or "artefact" not in a:
            r.fail("provenance", f"malformed fittedArtefacts entry: {a!r}")
            continue
        on = a.get("fittedOn")
        if on is None:
            r.fail(
                "provenance",
                f"fitted artefact {a['artefact']!r} records no fittedOn. "
                "'unverified' is a legal value and a recorded gap; absent is not.",
            )
        elif on not in ("training-partition-only", "unverified"):
            r.fail(
                "provenance",
                f"fitted artefact {a['artefact']!r} was fitted on {on!r}. Anything "
                "other than the training partition can see data the split excluded.",
            )
        elif on == "unverified":
            r.note(
                f"fitted artefact {a['artefact']!r} has unverified provenance -- "
                "recorded, and it belongs under 'Constraints traded away'."
            )


def check_outputs(rep: dict, r: Report) -> None:
    """Processed outputs are content-addressed so downstream can verify them."""
    outs = rep.get("outputs")
    if not outs:
        r.fail(
            "outputs",
            "processing report records no outputs. A training job cannot assert what "
            "it consumed against a report that does not say what was produced.",
        )
        return
    for o in outs:
        if not isinstance(o, dict) or "name" not in o:
            r.fail("outputs", f"malformed outputs entry: {o!r}")
            continue
        d = o.get("digest")
        if d in NULLISH:
            r.fail("outputs", f"output {o['name']!r} has no digest.")
        elif not DIGEST_RE.match(str(d)):
            r.fail(
                "outputs",
                f"output {o['name']!r} digest {d!r} is not a sha256 hex digest.",
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("manifest", type=Path, help="contracts/dataset-manifest.json")
    ap.add_argument(
        "report",
        type=Path,
        nargs="?",
        help="artifacts/processing-report.json (omit to check the manifest only)",
    )
    args = ap.parse_args()

    r = Report()
    manifest = load(args.manifest, r)
    if manifest is not None:
        check_manifest(manifest, r)

    report = None
    if args.report is not None:
        report = load(args.report, r)
        if report is not None:
            check_completeness(report, r)
            check_counts(report, manifest, r)
            check_groups(report, r)
            check_boundaries(report, r)
            check_fitted(report, r)
            check_outputs(report, r)

    for n in r.notes:
        print(f"NOTE  {n}")

    if r.ok:
        scope = "manifest" if report is None else "manifest and processing report"
        print(f"OK  {scope} satisfy the data-pipeline contract.")
        return 0

    print(f"\n{len(r.violations)} violation(s):", file=sys.stderr)
    for v in r.violations:
        print(f"  {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
