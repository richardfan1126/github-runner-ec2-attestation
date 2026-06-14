#!/usr/bin/env python3
"""Build-time helper: print the effective server configuration baked into the AMI.

Loads an ``EnvironmentFile``-style env file (the image's baked-in
``kiwi-descriptions/root/etc/github-actions-remote-executor/env``) through the
application's *own* ``load_config()`` — the single source of truth — and renders
every ``ServerConfig`` field as a GitHub-Flavored Markdown table on stdout.

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


def load_env_file(path: Path) -> None:
    """Populate ``os.environ`` from a systemd ``EnvironmentFile``-style file.

    Rules (matching how systemd loads ``EnvironmentFile=``): blank lines and lines
    starting with ``#`` are ignored; each remaining line is split on the first
    ``=``; the value is taken verbatim (the committed file uses simple unquoted
    values).
    """
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value


def render_table(config: ServerConfig) -> str:
    """Render every ``ServerConfig`` field as a GitHub-Flavored Markdown table.

    Rows are derived from ``dataclasses.fields()`` so the table cannot drift from
    the code and automatically covers every setting (including fields absent from
    the env file, which appear with their resolved defaults). Values are printed
    verbatim — no redaction (the source env file is committed in the repo).
    """
    lines = ["| Setting | Value |", "|---|---|"]
    for f in dataclasses.fields(config):
        lines.append(f"| {f.name} | {getattr(config, f.name)} |")
    return "\n".join(lines)


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
