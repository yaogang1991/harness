"""
Tests for #240: pre-run check for stdlib-shadowing directories.

Verifies _check_stdlib_shadowing detects and removes leftover directories
that shadow Python stdlib modules.
"""
import os
import pytest
from pathlib import Path
from unittest.mock import patch

from main import _check_stdlib_shadowing


class TestCheckStdlibShadowing:
    def test_detects_urllib_directory(self, tmp_path):
        """Detects urllib/ as stdlib shadow."""
        (tmp_path / "urllib").mkdir()
        (tmp_path / "urllib" / "__init__.py").write_text("# shadow", encoding="utf-8")
        with patch.dict(os.environ, {"HARNESS_NON_INTERACTIVE": "true"}):
            _check_stdlib_shadowing(str(tmp_path))
        # Should be removed in non-interactive mode
        assert not (tmp_path / "urllib").exists()

    def test_detects_json_directory(self, tmp_path):
        """Detects json/ as stdlib shadow."""
        (tmp_path / "json").mkdir()
        with patch.dict(os.environ, {"HARNESS_NON_INTERACTIVE": "true"}):
            _check_stdlib_shadowing(str(tmp_path))
        assert not (tmp_path / "json").exists()

    def test_preserves_non_stdlib_dirs(self, tmp_path):
        """Does not remove directories that don't shadow stdlib."""
        (tmp_path / "myapp").mkdir()
        (tmp_path / "tests").mkdir()
        with patch.dict(os.environ, {"HARNESS_NON_INTERACTIVE": "true"}):
            _check_stdlib_shadowing(str(tmp_path))
        assert (tmp_path / "myapp").exists()
        assert (tmp_path / "tests").exists()

    def test_skips_dot_dirs(self, tmp_path):
        """Skips hidden directories (e.g., .git, .harness)."""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".harness").mkdir()
        with patch.dict(os.environ, {"HARNESS_NON_INTERACTIVE": "true"}):
            _check_stdlib_shadowing(str(tmp_path))
        assert (tmp_path / ".git").exists()
        assert (tmp_path / ".harness").exists()

    def test_skips_underscore_dirs(self, tmp_path):
        """Skips __pycache__ and similar."""
        (tmp_path / "__pycache__").mkdir()
        with patch.dict(os.environ, {"HARNESS_NON_INTERACTIVE": "true"}):
            _check_stdlib_shadowing(str(tmp_path))
        assert (tmp_path / "__pycache__").exists()

    def test_no_action_when_no_project(self):
        """No-op when project is None."""
        with patch.dict(os.environ, {"HARNESS_NON_INTERACTIVE": "true"}):
            _check_stdlib_shadowing(None)  # Should not raise

    def test_no_action_when_project_missing(self):
        """No-op when project path doesn't exist."""
        with patch.dict(os.environ, {"HARNESS_NON_INTERACTIVE": "true"}):
            _check_stdlib_shadowing("/nonexistent/path")  # Should not raise

    def test_no_action_when_clean(self, tmp_path):
        """No-op when no shadowing directories exist."""
        (tmp_path / "myproject").mkdir()
        with patch.dict(os.environ, {"HARNESS_NON_INTERACTIVE": "true"}):
            _check_stdlib_shadowing(str(tmp_path))  # Should not raise

    def test_removes_multiple_shadows(self, tmp_path):
        """Removes all shadowing directories at once."""
        (tmp_path / "urllib").mkdir()
        (tmp_path / "json").mkdir()
        (tmp_path / "collections").mkdir()
        with patch.dict(os.environ, {"HARNESS_NON_INTERACTIVE": "true"}):
            _check_stdlib_shadowing(str(tmp_path))
        assert not (tmp_path / "urllib").exists()
        assert not (tmp_path / "json").exists()
        assert not (tmp_path / "collections").exists()

    def test_interactive_keep(self, tmp_path, capsys):
        """Interactive mode: user chooses to keep directories."""
        (tmp_path / "urllib").mkdir()
        with patch.dict(os.environ, {}, clear=True):
            with patch("builtins.input", return_value="n"):
                _check_stdlib_shadowing(str(tmp_path))
        assert (tmp_path / "urllib").exists()

    def test_interactive_remove(self, tmp_path, capsys):
        """Interactive mode: user chooses to remove."""
        (tmp_path / "urllib").mkdir()
        with patch.dict(os.environ, {}, clear=True):
            with patch("builtins.input", return_value="y"):
                _check_stdlib_shadowing(str(tmp_path))
        assert not (tmp_path / "urllib").exists()
