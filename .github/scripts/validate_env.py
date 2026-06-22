#!/usr/bin/env python3
"""Pre-bake validator for committed flavor env files (D11, D16).

Runs two checks over every ``flavors/default/env`` and ``flavors/<f>/env``
file supplied on the command line **before** any bake step runs:

1. **No bucket-③ keys** — rejects ``CONTAINER_IMAGE`` / ``CONTAINER_IMAGE_DIGEST``;
   those are pipeline outputs injected after image push, never committed values.
2. **No unknown keys** — rejects any key that is not in ``RECOGNIZED_ENV_KEYS``
   (derived from ``src/config.py``), so a misspelled or removed config key fails
   loudly instead of silently being dropped by ``load_config()``.

Exits with code 0 if all files are clean; 1 if any violation is found (all
files are checked before exiting so every error is reported in one pass).

Usage:
    uv run python .github/scripts/validate_env.py flavors/default/env flavors/rust-build/env

Exit codes:
    0  all supplied env files pass both checks
    1  one or more violations found; errors written to stderr
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import BUCKET_3_KEYS, RECOGNIZED_ENV_KEYS  # noqa: E402


def parse_env_keys(path: Path) -> list[tuple[int, str]]:
    """Return ``[(line_number, key), ...]`` for every KEY=VALUE line in *path*."""
    results = []
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        results.append((lineno, key))
    return results


def validate_file(path: Path) -> list[str]:
    """Return a list of error strings for *path*; empty list means clean."""
    if not path.is_file():
        return [f"{path}: file not found"]

    errors: list[str] = []
    for lineno, key in parse_env_keys(path):
        if key in BUCKET_3_KEYS:
            errors.append(
                f"{path}:{lineno}: bucket-③ key '{key}' must not appear in a committed "
                f"env file — it is a pipeline output injected after image push (D11)"
            )
        elif key not in RECOGNIZED_ENV_KEYS:
            errors.append(
                f"{path}:{lineno}: unknown key '{key}' — not in ServerConfig's "
                f"recognised-key set; check for a typo or update RECOGNIZED_ENV_KEYS "
                f"in src/config.py if this key was intentionally added (D16)"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("Usage: validate_env.py <env-file> [<env-file> ...]", file=sys.stderr)
        return 1

    all_errors: list[str] = []
    for arg in args:
        all_errors.extend(validate_file(Path(arg)))

    if all_errors:
        for err in all_errors:
            print(f"::error::{err}", file=sys.stderr)
        return 1

    checked = ", ".join(args)
    print(f"✓ All env files passed pre-bake validation: {checked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
