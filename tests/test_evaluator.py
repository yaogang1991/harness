"""
Tests for evaluator/engine.py — criterion checking, scoring, evaluation flow.
"""
import json
import pytest
from unittest.mock import MagicMock, patch

from core.models import EvaluationResult, SuccessCriterion, CriterionType
from evaluator.engine import EvaluatorEngine


@pytest.fixture
def tmp_store(tmp_path):
    from session.store import SessionStore
    return SessionStore(base_path=str(tmp_path / "events"))


@pytest.fixture
def evaluator(tmp_store):
    return EvaluatorEngine(tmp_store)


class TestCriterionChecking:
    def test_unrecognized_criterion(self, evaluator):
        """Unrecognized criteria return (False, msg, False)."""
        crit = SuccessCriterion(type=CriterionType.CUSTOM, description="code must be beautiful")
        passed, msg, auto = evaluator._check_criterion(crit, "/tmp/nonexistent")
        assert not passed
        assert not auto
        assert "not automatically checkable" in msg

    def test_extract_percentage(self, evaluator):
        assert evaluator._extract_percentage("coverage 80%") == 80
        assert evaluator._extract_percentage("no percentage here") is None
        assert evaluator._extract_percentage("need 95% coverage") == 95

    @patch("evaluator.engine.subprocess.run")
    def test_command_pass(self, mock_run, evaluator, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="2 passed")
        passed, msg = evaluator._run_command("echo ok", tmp_path)
        assert passed

    @patch("evaluator.engine.subprocess.run")
    def test_command_fail(self, mock_run, evaluator, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout="1 failed")
        passed, msg = evaluator._run_command("echo fail", tmp_path)
        assert not passed

    @patch("evaluator.engine.subprocess.run")
    def test_lint_clean(self, mock_run, evaluator, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        passed, msg = evaluator._run_lint(["."], tmp_path)
        assert passed

    @patch("evaluator.engine.subprocess.run")
    def test_lint_dirty(self, mock_run, evaluator, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout="E501 line too long")
        passed, msg = evaluator._run_lint(["."], tmp_path)
        assert not passed
        assert "issues" in msg.lower()

    def test_check_files_missing(self, evaluator, tmp_path):
        passed, msg = evaluator._check_files_exist(["missing.py"], tmp_path)
        assert not passed
        assert "missing" in msg.lower()

    def test_check_files_present(self, evaluator, tmp_path):
        (tmp_path / "exists.py").write_text("ok", encoding="utf-8")
        passed, msg = evaluator._check_files_exist(["exists.py"], tmp_path)
        assert passed


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
        assert not result.passed
        assert "manual review" in result.feedback.lower()

    def test_structured_file_exists(self, evaluator, tmp_path):
        (tmp_path / "hello.py").write_text("ok", encoding="utf-8")
        crit = json.dumps({"type": "file_exists", "path": "hello.py", "description": "file"})
        result = evaluator.evaluate_stage("s1", "impl", [crit], str(tmp_path))
        assert result.passed

    def test_structured_file_missing(self, evaluator, tmp_path):
        crit = json.dumps({"type": "file_exists", "path": "nope.py", "description": "file"})
        result = evaluator.evaluate_stage("s1", "impl", [crit], str(tmp_path))
        assert not result.passed
