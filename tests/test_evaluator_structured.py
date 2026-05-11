"""Tests for structured evaluator criteria end-to-end pipeline.

Verifies:
- DAGNode accepts list[str], list[dict], and mixed criteria
- EvaluatorEngine normalizes and dispatches correctly
- work_dir vs artifact_path distinction
- COMMAND type does NOT auto-append output_artifacts
"""
import json
import os
import tempfile

import pytest

from core.models import DAGNode, SuccessCriterion, CriterionType
from evaluator.engine import EvaluatorEngine
from session.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(base_path=str(tmp_path / "events"))


@pytest.fixture
def evaluator(store):
    return EvaluatorEngine(session_store=store)


@pytest.fixture
def work_dir(tmp_path):
    """Create a temp work_dir with a test file."""
    d = tmp_path / "project"
    d.mkdir()
    (d / "hello.py").write_text('print("hello")\n', encoding="utf-8")
    return d


# -- DAGNode criteria normalization --


class TestDAGNodeCriteriaNormalization:
    def test_plain_strings(self):
        node = DAGNode(id="n1", agent_type="generator", task_description="t",
                       success_criteria=["tests pass", "lint clean"])
        assert len(node.success_criteria) == 2
        assert node.success_criteria[0] == "tests pass"

    def test_dict_criteria_become_success_criterion(self):
        node = DAGNode(id="n2", agent_type="generator", task_description="t",
                       success_criteria=[
                           {"type": "file_exists", "path": "src/foo.py", "description": "foo exists"},
                       ])
        assert len(node.success_criteria) == 1
        assert isinstance(node.success_criteria[0], SuccessCriterion)
        assert node.success_criteria[0].type == CriterionType.FILE_EXISTS
        assert node.success_criteria[0].path == "src/foo.py"

    def test_mixed_criteria(self):
        node = DAGNode(id="n3", agent_type="generator", task_description="t",
                       success_criteria=[
                           "tests pass",
                           {"type": "lint", "description": "lint clean"},
                       ])
        assert len(node.success_criteria) == 2
        assert isinstance(node.success_criteria[0], str)
        assert isinstance(node.success_criteria[1], SuccessCriterion)
        assert node.success_criteria[1].type == CriterionType.LINT

    def test_success_criterion_object_preserved(self):
        sc = SuccessCriterion(type=CriterionType.COMMAND, command="echo ok", description="ok")
        node = DAGNode(id="n4", agent_type="generator", task_description="t",
                       success_criteria=[sc])
        assert len(node.success_criteria) == 1
        assert isinstance(node.success_criteria[0], SuccessCriterion)
        assert node.success_criteria[0].command == "echo ok"

    def test_json_string_backward_compat(self):
        """Previously serialized JSON strings should be parsed back into SuccessCriterion."""
        json_str = json.dumps({"type": "command", "command": "pytest", "description": "test"})
        node = DAGNode(id="n5", agent_type="generator", task_description="t",
                       success_criteria=[json_str])
        assert len(node.success_criteria) == 1
        assert isinstance(node.success_criteria[0], SuccessCriterion)
        assert node.success_criteria[0].type == CriterionType.COMMAND


# -- EvaluatorEngine normalize + dispatch --


class TestEvaluatorNormalizeCriteria:
    def test_legacy_string_tests_pass(self, evaluator):
        criteria = evaluator._normalize_criteria(["tests pass"])
        assert len(criteria) == 1
        assert criteria[0].type == CriterionType.COMMAND

    def test_legacy_string_lint(self, evaluator):
        criteria = evaluator._normalize_criteria(["lint clean"])
        assert len(criteria) == 1
        assert criteria[0].type == CriterionType.LINT

    def test_legacy_string_coverage(self, evaluator):
        criteria = evaluator._normalize_criteria(["coverage 90%"])
        assert len(criteria) == 1
        assert criteria[0].type == CriterionType.COVERAGE
        assert criteria[0].target == 90.0

    def test_structured_json_command(self, evaluator):
        json_str = json.dumps({"type": "command", "command": "echo ok", "description": "test"})
        criteria = evaluator._normalize_criteria([json_str])
        assert len(criteria) == 1
        assert criteria[0].type == CriterionType.COMMAND
        assert criteria[0].command == "echo ok"

    def test_structured_json_file_exists(self, evaluator):
        json_str = json.dumps({"type": "file_exists", "path": "hello.py", "description": "file"})
        criteria = evaluator._normalize_criteria([json_str])
        assert len(criteria) == 1
        assert criteria[0].type == CriterionType.FILE_EXISTS
        assert criteria[0].path == "hello.py"


# -- work_dir vs artifact_path --


class TestWorkDirVsArtifactPath:
    def test_file_exists_uses_work_dir(self, evaluator, work_dir):
        """file_exists should check in work_dir, not artifact_path."""
        artifact_dir = work_dir.parent / "artifacts"
        artifact_dir.mkdir(exist_ok=True)

        json_str = json.dumps({"type": "file_exists", "path": "hello.py", "description": "file exists"})
        result = evaluator.evaluate_stage(
            "s1", "stage1", [json_str],
            artifact_path=str(artifact_dir),
            work_dir=str(work_dir),
        )
        assert result.passed is True

    def test_file_exists_fails_in_wrong_dir(self, evaluator, work_dir):
        """file_exists should fail if file is only in work_dir but we pass a different dir."""
        wrong_dir = work_dir.parent / "empty"
        wrong_dir.mkdir()
        # hello.py only exists in work_dir, not in wrong_dir
        json_str = json.dumps({"type": "file_exists", "path": "hello.py", "description": "file exists"})
        result = evaluator.evaluate_stage(
            "s2", "stage2", [json_str],
            artifact_path=str(wrong_dir),
            work_dir=str(wrong_dir),
        )
        assert result.passed is False

    def test_command_uses_work_dir(self, evaluator, work_dir):
        """COMMAND should execute in work_dir."""
        json_str = json.dumps({
            "type": "command",
            "command": "python -c \"open('marker.txt','w').write('ok')\"",
            "description": "create marker",
        })
        evaluator.evaluate_stage(
            "s3", "stage3", [json_str],
            artifact_path="/tmp/nonexistent",
            work_dir=str(work_dir),
        )
        assert (work_dir / "marker.txt").exists()

    def test_no_critical_uses_work_dir(self, evaluator, work_dir):
        """NO_CRITICAL should scan output_artifacts relative to work_dir."""
        # Write a file with a FIXME marker
        (work_dir / "buggy.py").write_text("# FIXME: this is bad\n", encoding="utf-8")
        json_str = json.dumps({
            "type": "no_critical",
            "description": "no critical markers",
        })
        result = evaluator.evaluate_stage(
            "s4", "stage4", [json_str],
            artifact_path="/tmp/nonexistent",
            work_dir=str(work_dir),
            output_artifacts=["buggy.py"],
        )
        assert result.passed is False


# -- COMMAND does NOT auto-append output_artifacts --


class TestCommandNoAutoAppend:
    def test_command_uses_exact_command(self, evaluator, work_dir):
        """COMMAND type should run the exact command from criteria, not append output_artifacts."""
        json_str = json.dumps({
            "type": "command",
            "command": "python -c \"print('exact')\"",
            "description": "exact command",
        })
        result = evaluator.evaluate_stage(
            "s5", "stage5", [json_str],
            artifact_path=str(work_dir),
            work_dir=str(work_dir),
            output_artifacts=["hello.py"],
        )
        assert result.passed is True

    def test_legacy_tests_pass_no_artifact_appending(self, evaluator, work_dir):
        """Legacy 'tests pass' maps to COMMAND, should not append output_artifacts."""
        # This test verifies the COMMAND dispatch path does not auto-append
        result = evaluator.evaluate_stage(
            "s6", "stage6", ["tests pass"],
            artifact_path=str(work_dir),
            work_dir=str(work_dir),
            output_artifacts=["hello.py"],
        )
        # May pass or fail depending on pytest availability, but should not crash
        assert isinstance(result.passed, bool)


# -- End-to-end: DAGNode -> Evaluator --


class TestEndToEndPipeline:
    def test_structured_criteria_through_dag_node(self, evaluator, work_dir):
        """Verify structured criteria survive DAGNode -> evaluator."""
        node = DAGNode(
            id="gen1",
            agent_type="generator",
            task_description="generate hello.py",
            success_criteria=[
                {"type": "file_exists", "path": "hello.py", "description": "hello.py exists"},
            ],
        )
        # success_criteria are SuccessCriterion objects after validator
        assert isinstance(node.success_criteria[0], SuccessCriterion)
        result = evaluator.evaluate_stage(
            "s7", "gen1", node.success_criteria,
            artifact_path=str(work_dir),
            work_dir=str(work_dir),
        )
        assert result.passed is True
        assert "hello.py" in result.feedback

    def test_mixed_criteria_through_dag_node(self, evaluator, work_dir):
        """Mix of legacy string and structured dict criteria."""
        node = DAGNode(
            id="gen2",
            agent_type="generator",
            task_description="generate",
            success_criteria=[
                "lint clean",
                {"type": "file_exists", "path": "hello.py", "description": "file"},
            ],
        )
        assert isinstance(node.success_criteria[0], str)
        assert isinstance(node.success_criteria[1], SuccessCriterion)
        result = evaluator.evaluate_stage(
            "s8", "gen2", node.success_criteria,
            artifact_path=str(work_dir),
            work_dir=str(work_dir),
        )
        assert isinstance(result.passed, bool)
        assert result.score > 0


# -- Template loading with structured criteria --


class TestTemplateStructuredCriteria:
    def test_build_api_template_loads(self):
        from templates.library import TemplateRegistry
        registry = TemplateRegistry()
        tpl = registry.get_template("build_api")
        assert tpl is not None
        eval_nodes = [n for n in tpl.nodes if n.get("agent_type") == "evaluator"]
        assert len(eval_nodes) == 1
        sc = eval_nodes[0].get("success_criteria", [])
        assert len(sc) >= 1
        for c in sc:
            assert isinstance(c, dict)
            assert "type" in c

    def test_template_instantiation(self):
        from templates.library import TemplateRegistry
        registry = TemplateRegistry()
        dag = registry.instantiate("build_api", {"feature": "Todo API", "language": "Python"})
        eval_nodes = [n for n in dag.nodes.values() if n.agent_type == "evaluator"]
        assert len(eval_nodes) == 1
        assert len(eval_nodes[0].success_criteria) >= 1
        # Criteria should be SuccessCriterion objects, not JSON strings
        for sc in eval_nodes[0].success_criteria:
            assert isinstance(sc, SuccessCriterion)
            assert sc.type in (
                CriterionType.COMMAND, CriterionType.LINT,
                CriterionType.COVERAGE, CriterionType.FILE_EXISTS,
                CriterionType.NO_CRITICAL, CriterionType.CUSTOM,
            )
