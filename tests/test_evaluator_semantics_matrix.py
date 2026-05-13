"""Semantic matrix tests for evaluator criterion behavior (#269).

Validates the M1 exit criteria: every criterion's PASS/FAIL/WARN behavior
is tested at its semantic boundary.
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.models import CriterionType, EvaluationResult, SuccessCriterion
from evaluator.engine import EvaluatorEngine


def _make_engine(pass_threshold: float | None = None) -> EvaluatorEngine:
    mock_store = MagicMock()
    mock_store.emit_event = MagicMock()
    return EvaluatorEngine(session_store=mock_store, pass_threshold=pass_threshold)


# ------------------------------------------------------------------
# Matrix 1: TESTS_PASS + no scoped tests → WARN
# ------------------------------------------------------------------

class TestTestsPassNoScopedTests:
    """No test_path, no test files in output_artifacts → WARN."""

    def test_returns_was_auto_false(self):
        engine = _make_engine()
        passed, msg, was_auto = engine._check_criterion(
            SuccessCriterion(type=CriterionType.TESTS_PASS, description="tests pass"),
            "/nonexistent/work",
        )
        assert passed is True
        assert was_auto is False
        assert "not verified" in msg.lower()

    def test_through_evaluate_stage_is_uncheckable(self, tmp_path):
        engine = _make_engine()
        result = engine.evaluate_stage(
            session_id="s1", stage_name="impl",
            criteria=["tests pass"],
            artifact_path=str(tmp_path),
            work_dir=str(tmp_path),
        )
        assert "tests pass" in result.suggestions


# ------------------------------------------------------------------
# Matrix 2: TESTS_PASS + scoped tests → only run scoped
# ------------------------------------------------------------------

class TestTestsPassScopedTests:
    """When output_artifacts has test files, only those are run."""

    def test_scoped_test_file_is_auto_checked(self, tmp_path):
        (tmp_path / "test_app.py").write_text("def test_ok(): pass", encoding="utf-8")
        engine = _make_engine()
        passed, msg, was_auto = engine._check_criterion(
            SuccessCriterion(type=CriterionType.TESTS_PASS, description="tests pass"),
            str(tmp_path),
            output_artifacts=["test_app.py"],
        )
        assert was_auto is True

    def test_scoped_test_runs_only_specified_files(self, tmp_path):
        """Ensure pytest receives only the scoped file, not all tests."""
        (tmp_path / "test_app.py").write_text("def test_ok(): pass", encoding="utf-8")
        # An unrelated test that would fail if run
        (tmp_path / "test_other.py").write_text(
            "def test_fail(): assert False", encoding="utf-8"
        )
        engine = _make_engine()
        passed, msg, was_auto = engine._check_criterion(
            SuccessCriterion(type=CriterionType.TESTS_PASS, description="tests pass"),
            str(tmp_path),
            output_artifacts=["test_app.py"],
        )
        # Should pass because only test_app.py is scoped, not test_other.py
        assert passed is True
        assert was_auto is True


# ------------------------------------------------------------------
# Matrix 3: TESTS_PASS + leftover unrelated tests → not collected
# ------------------------------------------------------------------

class TestTestsPassLeftoverTests:
    """output_artifacts does NOT include leftover test files."""

    def test_leftover_tests_not_in_scope(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
        # Leftover test from previous run — not in output_artifacts
        (tmp_path / "test_leftover.py").write_text(
            "def test_leftover(): assert False", encoding="utf-8"
        )
        engine = _make_engine()
        passed, msg, was_auto = engine._check_criterion(
            SuccessCriterion(type=CriterionType.TESTS_PASS, description="tests pass"),
            str(tmp_path),
            output_artifacts=["app.py"],
        )
        # app.py is not a test file → no test files in scope → WARN
        assert was_auto is False
        assert "not verified" in msg.lower()


# ------------------------------------------------------------------
# Matrix 4: TEST_FILE_EXISTS + no test artifact → FAIL
# ------------------------------------------------------------------

class TestTestFileExistsMissing:
    """TEST_FILE_EXISTS fails when no test file is on disk."""

    def test_no_test_file_fails(self, tmp_path):
        engine = _make_engine()
        passed, msg, was_auto = engine._check_criterion(
            SuccessCriterion(
                type=CriterionType.TEST_FILE_EXISTS,
                description="test file exists",
            ),
            str(tmp_path),
        )
        assert passed is False
        assert was_auto is True

    def test_non_test_file_does_not_count(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
        engine = _make_engine()
        passed, msg, was_auto = engine._check_criterion(
            SuccessCriterion(
                type=CriterionType.TEST_FILE_EXISTS,
                description="test file exists",
            ),
            str(tmp_path),
            output_artifacts=["app.py"],
        )
        assert passed is False
        assert was_auto is True


# ------------------------------------------------------------------
# Matrix 5: TEST_FILE_EXISTS + tests/test_x.py → PASS
# ------------------------------------------------------------------

class TestTestFileExistsPresent:
    """TEST_FILE_EXISTS passes when a test file exists on disk."""

    def test_test_file_present(self, tmp_path):
        (tmp_path / "test_app.py").write_text("def test_ok(): pass", encoding="utf-8")
        engine = _make_engine()
        passed, msg, was_auto = engine._check_criterion(
            SuccessCriterion(
                type=CriterionType.TEST_FILE_EXISTS,
                description="test file exists",
            ),
            str(tmp_path),
            output_artifacts=["test_app.py"],
        )
        assert passed is True
        assert was_auto is True

    def test_zero_byte_test_file_passes(self, tmp_path):
        """Empty test file counts as existing (e.g. __init__.py)."""
        (tmp_path / "test_empty.py").write_text("", encoding="utf-8")
        engine = _make_engine()
        passed, msg, was_auto = engine._check_criterion(
            SuccessCriterion(
                type=CriterionType.TEST_FILE_EXISTS,
                description="test file exists",
            ),
            str(tmp_path),
            output_artifacts=["test_empty.py"],
        )
        assert passed is True


# ------------------------------------------------------------------
# Matrix 6: COVERAGE + no output_artifacts → WARN
# ------------------------------------------------------------------

class TestCoverageNoArtifacts:
    """COVERAGE with no output_artifacts behavior.

    When output_artifacts is None, _check_coverage runs pytest without --cov.
    If pytest collects 0 tests (empty dir), coverage is unverifiable.
    Current behavior: FAIL with "Tests failed and coverage report could not
    be parsed" (was_auto=True). This is a known semantic gap — coverage
    without scope should ideally WARN, but that fix belongs in a separate PR.
    """

    def test_no_output_artifacts_coverage_fails_gracefully(self, tmp_path):
        engine = _make_engine()
        result = engine.evaluate_stage(
            session_id="s1", stage_name="impl",
            criteria=["coverage >= 80%"],
            artifact_path=str(tmp_path),
            work_dir=str(tmp_path),
            output_artifacts=None,
        )
        # Current behavior: pytest runs without --cov, collects 0 tests,
        # fails with no TOTAL line → was_auto=True → FAIL
        # This test documents the current behavior, not the ideal behavior.
        assert "coverage >= 80%" in result.criteria_results
        assert result.criteria_results["coverage >= 80%"] is False

    def test_no_output_artifacts_with_test_file_returns_warn(self, tmp_path):
        """When tests exist but no output_artifacts, coverage is uncheckable (WARN)."""
        (tmp_path / "test_app.py").write_text("def test_ok(): assert True", encoding="utf-8")
        engine = _make_engine()
        result = engine.evaluate_stage(
            session_id="s1", stage_name="impl",
            criteria=["coverage >= 80%"],
            artifact_path=str(tmp_path),
            work_dir=str(tmp_path),
            output_artifacts=None,
        )
        # Tests pass but no --cov → no TOTAL line → WARN (was_auto=False)
        assert result.criteria_results["coverage >= 80%"] is True
        assert "coverage >= 80%" in result.suggestions


# ------------------------------------------------------------------
# Matrix 7: pass_threshold + hard criterion fail → FAIL
# ------------------------------------------------------------------

class TestPassThresholdHardFail:
    """Hard criterion failure cannot be overridden by pass_threshold."""

    def test_tests_pass_hard_fail_overrides_threshold(self, tmp_path):
        """Even with high pass_threshold, TESTS_PASS failure → overall FAIL."""
        engine = _make_engine(pass_threshold=5.0)
        # Create a test file that will fail
        (tmp_path / "test_fail.py").write_text(
            "def test_fail(): assert False", encoding="utf-8"
        )
        result = engine.evaluate_stage(
            session_id="s1", stage_name="impl",
            criteria=["tests pass"],
            artifact_path=str(tmp_path),
            work_dir=str(tmp_path),
            output_artifacts=["test_fail.py"],
        )
        assert result.passed is False


# ------------------------------------------------------------------
# Matrix 8: pass_threshold + WARN only → WARN doesn't cause FAIL
# ------------------------------------------------------------------

class TestPassThresholdWarnOnly:
    """Uncheckable (WARN) criteria should not block overall pass."""

    def test_uncheckable_does_not_block_pass(self, tmp_path):
        engine = _make_engine()
        # CUSTOM criterion is always WARN
        result = engine.evaluate_stage(
            session_id="s1", stage_name="impl",
            criteria=["code must be elegant"],
            artifact_path=str(tmp_path),
            work_dir=str(tmp_path),
        )
        # CUSTOM criterion → WARN → suggestions list
        # But overall_passed should be True (WARN doesn't fail)
        assert result.passed is True
        assert "code must be elegant" in result.suggestions
