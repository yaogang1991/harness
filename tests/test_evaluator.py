"""
Tests for evaluator/engine.py — criterion checking, scoring, evaluation flow.
"""
import pytest
from unittest.mock import MagicMock, patch

from core.models import EvaluationResult
from evaluator.engine import EvaluatorEngine


@pytest.fixture
def evaluator(tmp_store):
    return EvaluatorEngine(tmp_store)


class TestCriterionChecking:
    def test_unrecognized_criterion(self, evaluator):
        """Unrecognized criteria return (False, msg, False)."""
        passed, msg, auto = evaluator._check_criterion(
            "code must be beautiful", "/tmp/nonexistent"
        )
        assert not passed
        assert not auto
        assert "not automatically checkable" in msg

    def test_extract_percentage(self, evaluator):
        assert evaluator._extract_percentage("coverage 80%") == 80
        assert evaluator._extract_percentage("no percentage here") is None
        assert evaluator._extract_percentage("need 95% coverage") == 95

    @patch("evaluator.engine.subprocess.run")
    def test_tests_pass(self, mock_run, evaluator, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="2 passed")
        passed, msg = evaluator._run_tests(tmp_path)
        assert passed
        assert "passed" in msg.lower()

    @patch("evaluator.engine.subprocess.run")
    def test_tests_fail(self, mock_run, evaluator, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout="1 failed")
        passed, msg = evaluator._run_tests(tmp_path)
        assert not passed

    @patch("evaluator.engine.subprocess.run")
    def test_lint_clean(self, mock_run, evaluator, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        passed, msg = evaluator._run_lint(tmp_path)
        assert passed

    @patch("evaluator.engine.subprocess.run")
    def test_lint_dirty(self, mock_run, evaluator, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout="E501 line too long")
        passed, msg = evaluator._run_lint(tmp_path)
        assert not passed
        assert "issues" in msg.lower()

    def test_check_files_missing(self, evaluator, tmp_path):
        passed, msg = evaluator._check_files_exist("file exist: missing.py", tmp_path)
        assert not passed
        assert "missing" in msg.lower()


class TestEvaluateStage:
    @patch("evaluator.engine.subprocess.run")
    def test_all_pass(self, mock_run, evaluator, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="OK\nTOTAL    100%")
        result = evaluator.evaluate_stage(
            "s1", "impl", ["tests pass", "lint clean"], str(tmp_path)
        )
        assert isinstance(result, EvaluationResult)
        assert result.passed
        assert result.score > 0

    def test_uncheckable_criterion_fails(self, evaluator, tmp_path):
        result = evaluator.evaluate_stage(
            "s1", "impl", ["code must be elegant"], str(tmp_path)
        )
        assert not result.passed
        assert "not automatically" in result.feedback.lower()

    @patch("evaluator.engine.subprocess.run")
    def test_mix_pass_and_uncheckable(self, mock_run, evaluator, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="OK")
        result = evaluator.evaluate_stage(
            "s1", "impl", ["tests pass", "code must follow SOLID principles"], str(tmp_path)
        )
        # Has uncheckable criteria → not fully passed
        assert not result.passed
        assert "manual review" in result.feedback.lower()
