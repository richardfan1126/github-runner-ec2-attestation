"""Unit tests for the detect-changes script (.github/scripts/detect_changes.py).

Covers task 8.3:
  - Flavor enumeration excludes 'default' explicitly
  - detect-changes mapping for each invalidation level (global, image, ami-only)
  - Fail-safe / loop-guard edge cases (no-diff-baseline, empty changed, flavors.lock-only)
"""
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "detect_changes", REPO_ROOT / ".github" / "scripts" / "detect_changes.py"
)
detect_changes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(detect_changes)

KNOWN_FLAVORS = ["alpha", "beta"]


# ---------------------------------------------------------------------------
# Flavor enumeration
# ---------------------------------------------------------------------------

class TestEnumerateFlavors:
    def test_excludes_default_directory(self, tmp_path):
        (tmp_path / "default").mkdir()
        (tmp_path / "rust-build").mkdir()
        result = detect_changes.enumerate_flavors(tmp_path)
        assert "default" not in result
        assert "rust-build" in result

    def test_includes_all_non_default_dirs(self, tmp_path):
        for name in ("alpha", "beta", "gamma"):
            (tmp_path / name).mkdir()
        assert set(detect_changes.enumerate_flavors(tmp_path)) == {"alpha", "beta", "gamma"}

    def test_returns_sorted_list(self, tmp_path):
        for name in ("zebra", "alpha", "middle"):
            (tmp_path / name).mkdir()
        result = detect_changes.enumerate_flavors(tmp_path)
        assert result == sorted(result)

    def test_empty_flavors_dir_returns_empty(self, tmp_path):
        assert detect_changes.enumerate_flavors(tmp_path) == []

    def test_only_default_returns_empty(self, tmp_path):
        (tmp_path / "default").mkdir()
        assert detect_changes.enumerate_flavors(tmp_path) == []

    def test_missing_dir_returns_empty(self, tmp_path):
        assert detect_changes.enumerate_flavors(tmp_path / "nonexistent") == []


# ---------------------------------------------------------------------------
# Global invalidators → all flavors, image level
# ---------------------------------------------------------------------------

class TestGlobalInvalidators:
    @pytest.mark.parametrize("path", [
        ".github/scripts/detect_changes.py",
        ".github/scripts/print_config.py",
        ".github/docker/Dockerfile.kiwi-builder",
        "kiwi-descriptions/appliance.kiwi",
        "kiwi-descriptions/config.sh",
        "src/config.py",
        "src/server.py",
        "uv.lock",
        "pyproject.toml",
        "appliance.kiwi",
        ".github/workflows/build-attestable-image.yml",
        "flavors/default/env",
        "flavors/default/other-shared-file",
    ])
    def test_global_invalidator_rebuilds_all_at_image_level(self, path):
        result = detect_changes.compute_matrix([path], KNOWN_FLAVORS, None, False)
        assert sorted(result["image_flavors"]) == KNOWN_FLAVORS
        assert result["ami_only_flavors"] == []
        assert "global-invalidator" in result["reason"]

    def test_global_invalidator_takes_priority_over_per_flavor_path(self):
        paths = ["src/config.py", "flavors/alpha/Dockerfile"]
        result = detect_changes.compute_matrix(paths, KNOWN_FLAVORS, None, False)
        assert sorted(result["image_flavors"]) == KNOWN_FLAVORS
        assert "global-invalidator" in result["reason"]


# ---------------------------------------------------------------------------
# Per-flavor path mapping
# ---------------------------------------------------------------------------

class TestPerFlavorMapping:
    def test_non_env_file_triggers_image_level(self):
        result = detect_changes.compute_matrix(
            ["flavors/alpha/Dockerfile"], KNOWN_FLAVORS, None, False
        )
        assert result["image_flavors"] == ["alpha"]
        assert result["ami_only_flavors"] == []
        assert result["reason"] == "path-map"

    def test_env_file_triggers_ami_only(self):
        result = detect_changes.compute_matrix(
            ["flavors/alpha/env"], KNOWN_FLAVORS, None, False
        )
        assert result["ami_only_flavors"] == ["alpha"]
        assert result["image_flavors"] == []
        assert result["reason"] == "path-map"

    def test_image_level_beats_ami_only_for_same_flavor(self):
        """Same flavor with both Dockerfile and env changes → image level (not ami-only)."""
        result = detect_changes.compute_matrix(
            ["flavors/alpha/Dockerfile", "flavors/alpha/env"], KNOWN_FLAVORS, None, False
        )
        assert "alpha" in result["image_flavors"]
        assert "alpha" not in result["ami_only_flavors"]

    def test_different_flavors_can_have_different_levels(self):
        """alpha has a Dockerfile change; beta has only an env change."""
        result = detect_changes.compute_matrix(
            ["flavors/alpha/Dockerfile", "flavors/beta/env"], KNOWN_FLAVORS, None, False
        )
        assert result["image_flavors"] == ["alpha"]
        assert result["ami_only_flavors"] == ["beta"]

    def test_unknown_flavor_path_is_ignored(self):
        result = detect_changes.compute_matrix(
            ["flavors/unknown-flavor/Dockerfile"], KNOWN_FLAVORS, None, False
        )
        assert result["image_flavors"] == []
        assert result["ami_only_flavors"] == []


# ---------------------------------------------------------------------------
# Fail-safe / edge cases
# ---------------------------------------------------------------------------

class TestFailSafeAndEdgeCases:
    def test_no_diff_baseline_builds_all_at_image_level(self):
        result = detect_changes.compute_matrix([], KNOWN_FLAVORS, None, True)
        assert sorted(result["image_flavors"]) == KNOWN_FLAVORS
        assert result["ami_only_flavors"] == []
        assert result["reason"] == "no-diff-baseline"

    def test_empty_changed_set_skips_all(self):
        result = detect_changes.compute_matrix([], KNOWN_FLAVORS, None, False)
        assert result["image_flavors"] == []
        assert result["ami_only_flavors"] == []
        assert result["reason"] == "no-changes"

    def test_flavors_lock_only_triggers_loop_guard(self):
        """When only flavors.lock changed the write-back loop guard fires and skips all."""
        result = detect_changes.compute_matrix(
            ["flavors.lock"], KNOWN_FLAVORS, None, False
        )
        assert result["image_flavors"] == []
        assert result["ami_only_flavors"] == []
        assert "loop-guard" in result["reason"]

    def test_loop_guard_does_not_fire_when_other_files_also_changed(self):
        """flavors.lock + another path = real change; loop guard must NOT trigger."""
        result = detect_changes.compute_matrix(
            ["flavors.lock", "flavors/alpha/env"], KNOWN_FLAVORS, None, False
        )
        assert "loop-guard" not in result["reason"]

    def test_force_flavor_specific(self):
        result = detect_changes.compute_matrix([], KNOWN_FLAVORS, "alpha", False)
        assert result["image_flavors"] == ["alpha"]
        assert result["ami_only_flavors"] == []
        assert "force-flavor:alpha" in result["reason"]

    def test_force_all_rebuilds_all_flavors(self):
        result = detect_changes.compute_matrix([], KNOWN_FLAVORS, "all", False)
        assert sorted(result["image_flavors"]) == KNOWN_FLAVORS
        assert result["reason"] == "force-all"


# ---------------------------------------------------------------------------
# main() integration (reads changed-files, emits JSON)
# ---------------------------------------------------------------------------

class TestMainIntegration:
    def test_main_emits_valid_json(self, tmp_path, capsys):
        flavors_dir = tmp_path / "flavors"
        (flavors_dir / "alpha").mkdir(parents=True)
        (flavors_dir / "default").mkdir()
        changed = tmp_path / "changed.txt"
        changed.write_text("flavors/alpha/Dockerfile\n")

        rc = detect_changes.main([
            "--changed-files", str(changed),
            "--flavors-dir", str(flavors_dir),
        ])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "image_matrix" in data
        assert "ami_only_matrix" in data
        assert "reason" in data

    def test_main_no_diff_baseline_flag(self, tmp_path, capsys):
        flavors_dir = tmp_path / "flavors"
        (flavors_dir / "alpha").mkdir(parents=True)
        (flavors_dir / "default").mkdir()

        rc = detect_changes.main([
            "--no-diff-baseline",
            "--flavors-dir", str(flavors_dir),
        ])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["reason"] == "no-diff-baseline"
        assert any(e["flavor"] == "alpha" for e in data["image_matrix"]["include"])

    def test_main_default_excluded_from_enumeration(self, tmp_path, capsys):
        """main() must never include 'default' in the emitted matrix."""
        flavors_dir = tmp_path / "flavors"
        (flavors_dir / "default").mkdir(parents=True)
        (flavors_dir / "myflav").mkdir()
        changed = tmp_path / "changed.txt"
        changed.write_text("src/config.py\n")

        detect_changes.main([
            "--changed-files", str(changed),
            "--flavors-dir", str(flavors_dir),
        ])
        data = json.loads(capsys.readouterr().out)
        all_emitted = [
            e["flavor"]
            for matrix_key in ("image_matrix", "ami_only_matrix", "ami_matrix")
            for e in data[matrix_key]["include"]
        ]
        assert "default" not in all_emitted
