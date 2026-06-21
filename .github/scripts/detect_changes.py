#!/usr/bin/env python3
"""Build-time tool: map changed paths to a per-flavor build matrix (D12).

Reads a list of changed files and emits two JSON matrices for GitHub Actions:
one for image-level rebuilds, one for AMI-only rebuilds.

Invalidation rules (D12):
  Global invalidators → ALL known flavors, image level:
    .github/scripts/**   .github/docker/**   kiwi-descriptions/**
    src/**               uv.lock             pyproject.toml
    appliance.kiwi       .github/workflows/**  flavors/default/**

  Per-flavor, image level:   flavors/<f>/* (anything except env)
  Per-flavor, AMI-only:      flavors/<f>/env

Fail-safe / edge cases:
  --no-diff-baseline      → ALL flavors, image level (new branch / force push)
  --force-flavor <n|all>  → override (workflow_dispatch)
  changed = {flavors.lock} only → empty matrix (write-back loop guard)
  changed = {}             → empty matrix

Usage:
    uv run python .github/scripts/detect_changes.py \\
        [--changed-files changed.txt | -] \\
        [--force-flavor rust-build | all] \\
        [--no-diff-baseline] \\
        [--summary-file detect_summary.md]

Output (stdout): JSON object with image_matrix, ami_only_matrix, reason, etc.
Exit codes:
    0  matrix computed successfully
    1  unknown flavor specified via --force-flavor
"""
import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Patterns for global invalidators — changes matching any of these rebuild ALL
# known flavors at image level.  Order does not matter; first match wins.
GLOBAL_INVALIDATOR_PATTERNS: list[str] = [
    r"^\.github/scripts/",
    r"^\.github/docker/",
    r"^kiwi-descriptions/",
    r"^src/",
    r"^uv\.lock$",
    r"^pyproject\.toml$",
    r"^appliance\.kiwi$",
    r"^\.github/workflows/",
    r"^flavors/default/",
]

_LOCKFILE_RE = re.compile(r"^flavors\.lock$")
_FLAVOR_PATH_RE = re.compile(r"^flavors/([^/]+)/(.+)$")


def enumerate_flavors(flavors_dir: Path) -> list[str]:
    """Return sorted list of buildable flavors: ls flavors/ minus 'default'."""
    if not flavors_dir.is_dir():
        return []
    return sorted(d.name for d in flavors_dir.iterdir() if d.is_dir() and d.name != "default")


def _is_global_invalidator(path: str) -> bool:
    return any(re.match(p, path) for p in GLOBAL_INVALIDATOR_PATTERNS)


def _flavor_and_level(path: str, known_flavors: set[str]) -> tuple[str, str] | None:
    """Return (flavor, 'image'|'ami-only') for a per-flavor path, else None."""
    m = _FLAVOR_PATH_RE.match(path)
    if not m:
        return None
    flavor, sub = m.group(1), m.group(2)
    if flavor not in known_flavors:
        return None
    return (flavor, "ami-only" if sub == "env" else "image")


def compute_matrix(
    changed_files: list[str],
    flavors: list[str],
    force_flavor: str | None,
    no_diff_baseline: bool,
) -> dict[str, object]:
    """Return dict with image_flavors, ami_only_flavors, and reason."""
    known = set(flavors)

    # Override: --force-flavor
    if force_flavor:
        if force_flavor == "all":
            return dict(image_flavors=flavors[:], ami_only_flavors=[], reason="force-all")
        if force_flavor not in known:
            print(
                f"::error::Unknown flavor '{force_flavor}' (known: {', '.join(sorted(known))})",
                file=sys.stderr,
            )
            sys.exit(1)
        return dict(image_flavors=[force_flavor], ami_only_flavors=[], reason=f"force-flavor:{force_flavor}")

    # Fail-safe: no diff baseline (new branch / force push / workflow_dispatch without override)
    if no_diff_baseline:
        return dict(image_flavors=flavors[:], ami_only_flavors=[], reason="no-diff-baseline")

    # Empty changed set
    if not changed_files:
        return dict(image_flavors=[], ami_only_flavors=[], reason="no-changes")

    # Loop guard: ALL changed files are flavors.lock → skip; lock already current
    if all(_LOCKFILE_RE.match(f) for f in changed_files):
        return dict(image_flavors=[], ami_only_flavors=[], reason="loop-guard:flavors.lock-only")

    # Normal path classification
    image: set[str] = set()
    ami_only: set[str] = set()
    global_trigger: str | None = None

    for path in changed_files:
        if _is_global_invalidator(path):
            global_trigger = path
            break
        r = _flavor_and_level(path, known)
        if r is None:
            continue
        flavor, level = r
        if level == "image":
            image.add(flavor)
        else:
            ami_only.add(flavor)

    if global_trigger is not None:
        return dict(
            image_flavors=flavors[:],
            ami_only_flavors=[],
            reason=f"global-invalidator:{global_trigger}",
        )

    # Promote ami-only to image if the flavor also got an image-level trigger
    ami_only_final = sorted(f for f in ami_only if f not in image)
    return dict(
        image_flavors=sorted(image),
        ami_only_flavors=ami_only_final,
        reason="path-map",
    )


def _gha_matrix(flavors: list[str]) -> dict:
    return {"include": [{"flavor": f} for f in flavors]}


def _write_summary(
    flavors: list[str],
    image_flavors: list[str],
    ami_only_flavors: list[str],
    reason: str,
    summary_file: Path,
) -> None:
    lines = [
        "### Detect Changes — Rebuild Decision",
        "",
        f"**Trigger:** `{reason}`",
        f"**Known flavors:** {', '.join(f'`{f}`' for f in flavors) or '(none)'}",
    ]
    if image_flavors:
        lines.append(f"**Image-level rebuilds:** {', '.join(f'`{f}`' for f in image_flavors)}")
    if ami_only_flavors:
        lines.append(f"**AMI-only rebuilds:** {', '.join(f'`{f}`' for f in ami_only_flavors)}")
    if not image_flavors and not ami_only_flavors:
        lines.append("**No rebuilds needed** — skipping all flavor builds")
    lines.append("")
    summary_file.write_text("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute per-flavor build matrix from changed paths (D12)."
    )
    parser.add_argument(
        "--changed-files",
        default=None,
        help="Path to file with newline-separated changed paths, or '-' for stdin",
    )
    parser.add_argument(
        "--flavors-dir",
        type=Path,
        default=REPO_ROOT / "flavors",
        help="Root flavors directory (default: <repo-root>/flavors)",
    )
    parser.add_argument(
        "--force-flavor",
        default=None,
        help="Force rebuild: a specific flavor name or 'all' (workflow_dispatch override)",
    )
    parser.add_argument(
        "--no-diff-baseline",
        action="store_true",
        help="No diff baseline (new branch, force push, workflow_dispatch): build ALL",
    )
    parser.add_argument(
        "--summary-file",
        type=Path,
        default=None,
        help="Write Markdown rebuild decision here (append to $GITHUB_STEP_SUMMARY)",
    )
    args = parser.parse_args(argv)

    flavors = enumerate_flavors(args.flavors_dir)

    changed_files: list[str] = []
    if args.changed_files:
        if args.changed_files == "-":
            text = sys.stdin.read()
        else:
            text = Path(args.changed_files).read_text()
        changed_files = [line.strip() for line in text.splitlines() if line.strip()]

    result = compute_matrix(changed_files, flavors, args.force_flavor, args.no_diff_baseline)
    image_flavors: list[str] = result["image_flavors"]  # type: ignore[assignment]
    ami_only_flavors: list[str] = result["ami_only_flavors"]  # type: ignore[assignment]
    reason: str = result["reason"]  # type: ignore[assignment]

    all_ami_flavors = sorted(set(image_flavors) | set(ami_only_flavors))
    output = {
        "image_matrix": _gha_matrix(image_flavors),
        "ami_only_matrix": _gha_matrix(ami_only_flavors),
        "ami_matrix": _gha_matrix(all_ami_flavors),
        "all_flavors": flavors,
        "reason": reason,
        "has_image_builds": bool(image_flavors),
        "has_ami_only_builds": bool(ami_only_flavors),
        "has_ami_builds": bool(all_ami_flavors),
    }
    print(json.dumps(output))

    if args.summary_file:
        _write_summary(flavors, image_flavors, ami_only_flavors, reason, args.summary_file)

    return 0


if __name__ == "__main__":
    sys.exit(main())
