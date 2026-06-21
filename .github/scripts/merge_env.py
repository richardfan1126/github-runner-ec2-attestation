#!/usr/bin/env python3
"""Build-time tool: merge flavor env files into a single effective env (D7, D8).

Implements the fixed-precedence overlay chain:

    src/config.py defaults (bucket ①)
        ◀── flavors/default/env   (shared bucket-② declared values)
        ◀── flavors/<f>/env       (per-flavor deltas: auth keys, resource overrides)
        ◀── pipeline inject       (bucket ③: CONTAINER_IMAGE + CONTAINER_IMAGE_DIGEST)
              = effective env  →  output file  →  baked into verity root  →  PCR4

The output is an ``EnvironmentFile``-style ``KEY=VALUE`` file written to
``--output``.  No second config schema is introduced: all recognised keys are
those enumerated in ``RECOGNIZED_ENV_KEYS`` (derived from ``src/config.py``).

**Deny-all by design (D9)**: the merged file is validated through
``load_config()`` when both ``--container-image`` and ``--container-image-digest``
are provided (i.e., after the flavor's Dockerfile has been built and pushed —
task 4.1).  A flavor that declares no ``ALLOWED_REPOSITORIES`` /
``EXPECTED_AUDIENCE`` will cause ``load_config()`` to raise, failing the build
before any bake step.  When the bucket-③ arguments are omitted (pre-task-4
transitional builds), the merge is written but ``load_config()`` is not called.

Usage:
    # Full merge with bucket-③ injection (post-task-4.1):
    uv run python .github/scripts/merge_env.py \\
        --flavor rust-build \\
        --container-image ghcr.io/owner/repo/rust-build \\
        --container-image-digest sha256:... \\
        --output /path/to/effective.env

    # Partial merge without bucket-③ (transitional, pre-task-4.1):
    uv run python .github/scripts/merge_env.py \\
        --flavor rust-build \\
        --output /path/to/effective.env

Exit codes:
    0  merge successful (and load_config() passed if bucket-③ args supplied)
    1  error: missing files, unknown flavor, or load_config() validation failure
"""
import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import BUCKET_3_KEYS, ConfigurationError, load_config  # noqa: E402


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse an EnvironmentFile-style file into an ordered {key: value} dict."""
    result: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_surrounding_quotes(value)
        result[key] = value
    return result


def _strip_surrounding_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return value


def write_env_file(path: Path, merged: dict[str, str]) -> None:
    lines = [f"{k}={v}\n" for k, v in merged.items()]
    path.write_text("".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge flavor env files into an effective env for KIWI bake."
    )
    parser.add_argument("--flavor", required=True, help="Flavor name (e.g. rust-build)")
    parser.add_argument(
        "--flavors-dir",
        type=Path,
        default=REPO_ROOT / "flavors",
        help="Root flavors directory (default: <repo-root>/flavors)",
    )
    parser.add_argument("--output", required=True, type=Path, help="Output effective env path")
    parser.add_argument(
        "--container-image",
        default=None,
        help="Bucket-③ CONTAINER_IMAGE value (injected after image push, task 4.1)",
    )
    parser.add_argument(
        "--container-image-digest",
        default=None,
        help="Bucket-③ CONTAINER_IMAGE_DIGEST value (injected after image push, task 4.1)",
    )
    args = parser.parse_args(argv)

    flavors_dir: Path = args.flavors_dir
    flavor: str = args.flavor

    default_env = flavors_dir / "default" / "env"
    flavor_env = flavors_dir / flavor / "env"

    if not default_env.is_file():
        print(f"::error::flavors/default/env not found: {default_env}", file=sys.stderr)
        return 1
    if not flavor_env.is_file():
        print(f"::error::flavors/{flavor}/env not found: {flavor_env}", file=sys.stderr)
        return 1

    # Fixed-precedence merge: default ◀ flavor ◀ bucket-③ inject
    merged: dict[str, str] = {}
    merged.update(parse_env_file(default_env))
    merged.update(parse_env_file(flavor_env))

    # Bucket-③ injection (task 2.3): pipeline-supplied values win last
    have_bucket3 = args.container_image is not None and args.container_image_digest is not None
    if have_bucket3:
        merged["CONTAINER_IMAGE"] = args.container_image
        merged["CONTAINER_IMAGE_DIGEST"] = args.container_image_digest
    else:
        missing = [k for k in BUCKET_3_KEYS if k not in merged]
        if missing:
            print(
                f"::warning::merge_env: bucket-③ keys not injected ({', '.join(sorted(missing))}); "
                f"skipping load_config() validation (pass --container-image/--container-image-digest "
                f"after task 4.1 image push to enable full validation)",
                file=sys.stderr,
            )

    # Write effective env before validation so the file exists even on failure
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_env_file(args.output, merged)
    print(f"✓ Wrote effective env for flavor '{flavor}' → {args.output}")

    # Full validation through load_config() only when bucket-③ values are present
    if have_bucket3:
        for k, v in merged.items():
            os.environ[k] = v
        try:
            load_config()
        except (ConfigurationError, ValueError) as exc:
            print(f"::error::Effective env for flavor '{flavor}' failed validation: {exc}", file=sys.stderr)
            return 1
        print(f"✓ load_config() validation passed for flavor '{flavor}'")

    return 0


if __name__ == "__main__":
    sys.exit(main())
