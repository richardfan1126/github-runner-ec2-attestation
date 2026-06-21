#!/usr/bin/env python3
"""Build-time helper: print the effective server configuration baked into the AMI.

Loads an ``EnvironmentFile``-style env file (the flavor's generated effective env,
produced at build time by merging ``flavors/default/env`` ◀ ``flavors/<f>/env`` ◀
pipeline-injected bucket-③ values) through the application's *own* ``load_config()``
— the single source of truth — and renders every ``ServerConfig`` field on stdout as
GitHub-Flavored Markdown.

The output is **grouped by configuration category** into labeled ``####``
subsections in a stable, map-defined order (``CONFIG_CATEGORIES``), each its own
``| Setting | Value |`` table. Any field not assigned a category falls into a
catch-all ``Other`` subsection rendered **last** — so the field set stays derived
from ``dataclasses.fields(ServerConfig)`` and a newly added field surfaces under
``Other`` rather than being silently dropped (drift-proof; research Decision 11).

This is build tooling (it lives under ``.github/scripts/``, alongside
``build-kiwi-image.sh``), not part of the executor runtime under ``src/``. It
imports only ``src.config``: it never binds a port, touches the NitroTPM, or
pulls in FastAPI/Docker.

Usage:
    uv run python .github/scripts/print_config.py --env-file <path-to-env-file>

Exit codes:
    0  configuration resolved and validated; table written to stdout
    1  missing required var / invalid value / any ConfigurationError|ValueError;
       message written to stderr (so the build step can ::error:: and fail)
"""
import argparse
import dataclasses
import os
import sys
from pathlib import Path

# The repo root is the executor's uv project (pyproject `packages = ["src"]`).
# Running via `uv run` makes `src` importable; add the root to sys.path too so the
# helper also works under a plain `python .github/scripts/print_config.py`.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import ConfigurationError, ServerConfig, load_config  # noqa: E402

# Ordered map: category label -> ordered ServerConfig field names in that category.
# Subsections render in this order; within a subsection, fields render in listed
# order. Any field NOT listed here falls into a catch-all "Other" group rendered
# last — keeping the printed field set derived from dataclasses.fields() so a newly
# added field is never dropped (research Decision 11; build-config-summary-contract §1).
CONFIG_CATEGORIES: dict[str, list[str]] = {
    "HTTP Server": ["port"],
    "Execution": [
        "max_concurrent_executions",
        "execution_timeout_seconds",
        "max_script_size_bytes",
    ],
    "Rate Limiting": [
        "rate_limit_per_ip",
        "rate_limit_window_seconds",
        "max_output_attestations_per_window",
        "output_attestation_window_seconds",
    ],
    "Storage": ["temp_storage_path", "output_retention_hours"],
    "NitroTPM": ["tpm_attest_path", "allow_no_tpm"],
    "OIDC Authentication": [
        "allowed_repositories",
        "expected_audience",
        "allowed_branches",
        "require_protected_ref",
    ],
    "Container Execution": [
        "container_image",
        "container_image_digest",
        "container_memory_limit",
        "container_cpu_limit",
        "container_pids_limit",
    ],
    "GPU": ["enable_gpu", "gpu_devices", "nvidia_driver_capabilities"],
    "Container Security": [
        "container_user",
        "container_allow_root",
        "container_cap_add",
        "no_new_privileges",
        "container_read_only_rootfs",
        "container_tmpfs_size",
        "container_tmpfs_exec",
        "workspace_mount_mode",
        "container_network_mode",
    ],
}

_OTHER_LABEL = "Other"


def load_env_file(path: Path) -> None:
    """Populate ``os.environ`` from a systemd ``EnvironmentFile``-style file.

    Implements a *practical subset* of systemd ``EnvironmentFile=`` rules: blank
    lines and lines whose first non-whitespace character is ``#`` or ``;`` are
    ignored as comments; each remaining line is split on the first ``=``; a single
    matched pair of surrounding single or double quotes is stripped from the value
    (unquoted and unbalanced values are kept verbatim). Backslash line-continuation
    and in-value escapes are deliberately *not* handled (the committed file uses
    simple values); they would need adding if the file ever relied on them.
    """
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = _strip_surrounding_quotes(value)


def _strip_surrounding_quotes(value: str) -> str:
    """Strip one matched pair of surrounding single/double quotes; else return verbatim."""
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return value


def _category_table(label: str, field_names: list[str], config: ServerConfig) -> str:
    """Render one ``#### <label>`` subsection with its own `| Setting | Value |` table."""
    lines = [f"#### {label}", "", "| Setting | Value |", "|---|---|"]
    for name in field_names:
        lines.append(f"| {name} | {getattr(config, name)} |")
    return "\n".join(lines)


def render_table(config: ServerConfig) -> str:
    """Render every ``ServerConfig`` field grouped by category as Markdown subsections.

    Each category in ``CONFIG_CATEGORIES`` (in map order) that has at least one
    field becomes a ``#### <label>`` heading + its own table. Any field not assigned
    a category is collected into a catch-all ``Other`` subsection rendered last (only
    when non-empty). The field set is derived from ``dataclasses.fields()`` so every
    setting appears exactly once and a newly added field surfaces under ``Other``
    rather than being dropped. Values are printed verbatim — no redaction (the source
    env file is committed in the repo).
    """
    all_names = [f.name for f in dataclasses.fields(config)]
    categorized = {name for names in CONFIG_CATEGORIES.values() for name in names}

    sections: list[str] = []
    for label, field_names in CONFIG_CATEGORIES.items():
        present = [name for name in field_names if name in all_names]
        if present:
            sections.append(_category_table(label, present, config))

    other = [name for name in all_names if name not in categorized]
    if other:
        sections.append(_category_table(_OTHER_LABEL, other, config))

    return "\n\n".join(sections)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print the effective server configuration baked into the AMI as a Markdown table."
    )
    parser.add_argument(
        "--env-file",
        required=True,
        type=Path,
        help="Path to the EnvironmentFile-style env file to resolve.",
    )
    args = parser.parse_args(argv)

    if not args.env_file.is_file():
        print(f"Env file not found: {args.env_file}", file=sys.stderr)
        return 1

    load_env_file(args.env_file)

    try:
        config = load_config()
    except (ConfigurationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(render_table(config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
