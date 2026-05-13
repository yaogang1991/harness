"""
Tests for #271: Hard/soft dependency semantics on DAG edges.

Validates:
- DependencyType enum (HARD/SOFT)
- DAGEdge default is HARD (safe default)
- HARD dependency: upstream FAILED → downstream SKIP
- SOFT dependency: upstream FAILED → downstream continues with warning
- Default dependency type (no type specified) is HARD
- Soft dependency failed → warning artifact passed to downstream
- Planner can mark dependencies as soft in edge definitions
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from core.models import (
    DAG, DAGNode, DAGEdge, NodeStatus, DependencyType,
    HandoffArtifact,
)
from core.dag_engine import DAGExecutionEngine


def _make_node(nid: str, agent_type: str = "generator", **kw) -> DAGNode:
    return DAGNode(id=nid, agent_type=agent_type, task_description=f"task-{nid}", **kw)


def _make_dag(nodes: dict[str, DAGNode], edges: list[DAGEdge]) -> DAG:
    return DAG(nodes=nodes, edges=edges)


def _make_engine(**overrides) -> DAGExecutionEngine:
    defaults = {
        "agent_executor": AsyncMock(return_value={"artifacts": []}),
        "failure_handler": AsyncMock(return_value=MagicMock(action="abort", reasoning="test")),
        "evaluator": None,
        "enable_watchdog": False,
    }
    defaults.update(overrides)
    return DAGExecutionEngine(**defaults)


# =====================================================================
# DependencyType enum
# =====================================================================

class TestDependencyTypeEnum:
    def test_hard_value(self):
        assert DependencyType.HARD == "hard"

    def test_soft_value(self):
        assert DependencyType.SOFT == "soft"


# =====================================================================
# DAGEdge default
# =====================================================================

class TestDAGEdgeDefault:
    def test_default_is_hard(self):
        edge = DAGEdge(from_node="a", to_node="b")
        assert edge.dependency_type == DependencyType.HARD

    def test_explicit_soft(self):
        edge = DAGEdge(from_node="a", to_node="b", dependency_type=DependencyType.SOFT)
        assert edge.dependency_type == DependencyType.SOFT

    def test_explicit_hard(self):
        edge = DAGEdge(from_node="a", to_node="b", dependency_type=DependencyType.HARD)
        assert edge.dependency_type == DependencyType.HARD


# =====================================================================
# DAG.get_incoming_edges
# =====================================================================

class TestGetIncomingEdges:
    def test_returns_edges_with_types(self):
        dag = DAG(
            nodes={"a": _make_node("a"), "b": _make_node("b"), "c": _make_node("c")},
            edges=[
                DAGEdge(from_node="a", to_node="c", dependency_type=DependencyType.HARD),
                DAGEdge(from_node="b", to_node="c", dependency_type=DependencyType.SOFT),
            ],
        )
        incoming = dag.get_incoming_edges("c")
        assert len(incoming) == 2
        types = {e.from_node: e.dependency_type for e in incoming}
        assert types["a"] == DependencyType.HARD
        assert types["b"] == DependencyType.SOFT


# =====================================================================
# Hard dependency: upstream FAILED → downstream SKIP
# =====================================================================

class TestHardDependency:
    """HARD (default): upstream failure blocks downstream."""

    @pytest.mark.asyncio
    async def test_hard_dep_failed_upstream_skips(self):
        upstream = _make_node("gen", status=NodeStatus.FAILED, error="boom")
        downstream = _make_node("eval", agent_type="evaluator")
        dag = _make_dag(
            {"gen": upstream, "eval": downstream},
            [DAGEdge(from_node="gen", to_node="eval")],  # default HARD
        )
        engine = _make_engine()
        await engine._execute_single_node(dag, "eval")
        assert dag.nodes["eval"].status == NodeStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_explicit_hard_dep_failed_skips(self):
        upstream = _make_node("gen", status=NodeStatus.FAILED, error="boom")
        downstream = _make_node("eval", agent_type="evaluator")
        dag = _make_dag(
            {"gen": upstream, "eval": downstream},
            [DAGEdge(from_node="gen", to_node="eval", dependency_type=DependencyType.HARD)],
        )
        engine = _make_engine()
        await engine._execute_single_node(dag, "eval")
        assert dag.nodes["eval"].status == NodeStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_hard_dep_skipped_upstream_skips(self):
        upstream = _make_node("gen", status=NodeStatus.SKIPPED,
                              error="skipped due to upstream")
        downstream = _make_node("eval", agent_type="evaluator")
        dag = _make_dag(
            {"gen": upstream, "eval": downstream},
            [DAGEdge(from_node="gen", to_node="eval")],
        )
        engine = _make_engine()
        await engine._execute_single_node(dag, "eval")
        assert dag.nodes["eval"].status == NodeStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_hard_dep_success_upstream_continues(self):
        upstream = _make_node("gen", status=NodeStatus.SUCCESS,
                              result={"summary": "ok"}, output_artifacts=["a.py"])
        downstream = _make_node("eval", agent_type="evaluator")
        dag = _make_dag(
            {"gen": upstream, "eval": downstream},
            [DAGEdge(from_node="gen", to_node="eval")],
        )
        engine = _make_engine()
        await engine._execute_single_node(dag, "eval")
        assert dag.nodes["eval"].status != NodeStatus.SKIPPED


# =====================================================================
# Soft dependency: upstream FAILED → downstream continues
# =====================================================================

class TestSoftDependency:
    """SOFT: upstream failure does NOT block downstream."""

    @pytest.mark.asyncio
    async def test_soft_dep_failed_upstream_continues(self):
        upstream = _make_node("quality", status=NodeStatus.FAILED, error="quality check failed")
        downstream = _make_node("report", agent_type="generator")
        dag = _make_dag(
            {"quality": upstream, "report": downstream},
            [DAGEdge(from_node="quality", to_node="report",
                     dependency_type=DependencyType.SOFT)],
        )
        engine = _make_engine()
        await engine._execute_single_node(dag, "report")
        # Should NOT be skipped
        assert dag.nodes["report"].status != NodeStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_soft_dep_skipped_upstream_continues(self):
        upstream = _make_node("quality", status=NodeStatus.SKIPPED, error="skipped")
        downstream = _make_node("report", agent_type="generator")
        dag = _make_dag(
            {"quality": upstream, "report": downstream},
            [DAGEdge(from_node="quality", to_node="report",
                     dependency_type=DependencyType.SOFT)],
        )
        engine = _make_engine()
        await engine._execute_single_node(dag, "report")
        assert dag.nodes["report"].status != NodeStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_soft_dep_success_upstream_continues(self):
        upstream = _make_node("quality", status=NodeStatus.SUCCESS,
                              result={"summary": "ok"}, output_artifacts=["report.txt"])
        downstream = _make_node("report", agent_type="generator")
        dag = _make_dag(
            {"quality": upstream, "report": downstream},
            [DAGEdge(from_node="quality", to_node="report",
                     dependency_type=DependencyType.SOFT)],
        )
        engine = _make_engine()
        await engine._execute_single_node(dag, "report")
        assert dag.nodes["report"].status != NodeStatus.SKIPPED


# =====================================================================
# Mixed dependencies: one hard fails, one soft fails
# =====================================================================

class TestMixedDependencies:
    """Mix of hard and soft deps: hard failure still blocks."""

    @pytest.mark.asyncio
    async def test_hard_fail_overrides_soft_pass(self):
        """If any hard dep fails, node is skipped even if soft deps are ok."""
        hard_up = _make_node("gen", status=NodeStatus.FAILED, error="boom")
        soft_up = _make_node("quality", status=NodeStatus.SUCCESS,
                             result={"summary": "ok"}, output_artifacts=[])
        downstream = _make_node("eval", agent_type="evaluator")
        dag = _make_dag(
            {"gen": hard_up, "quality": soft_up, "eval": downstream},
            [
                DAGEdge(from_node="gen", to_node="eval",
                        dependency_type=DependencyType.HARD),
                DAGEdge(from_node="quality", to_node="eval",
                        dependency_type=DependencyType.SOFT),
            ],
        )
        engine = _make_engine()
        await engine._execute_single_node(dag, "eval")
        assert dag.nodes["eval"].status == NodeStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_soft_fail_hard_pass_continues(self):
        """If only soft deps fail, node continues."""
        hard_up = _make_node("gen", status=NodeStatus.SUCCESS,
                             result={"summary": "ok"}, output_artifacts=["a.py"])
        soft_up = _make_node("quality", status=NodeStatus.FAILED, error="quality fail")
        downstream = _make_node("report", agent_type="generator")
        dag = _make_dag(
            {"gen": hard_up, "quality": soft_up, "report": downstream},
            [
                DAGEdge(from_node="gen", to_node="report",
                        dependency_type=DependencyType.HARD),
                DAGEdge(from_node="quality", to_node="report",
                        dependency_type=DependencyType.SOFT),
            ],
        )
        engine = _make_engine()
        await engine._execute_single_node(dag, "report")
        assert dag.nodes["report"].status != NodeStatus.SKIPPED


# =====================================================================
# Soft dependency warning artifact
# =====================================================================

class TestSoftDepWarningArtifact:
    """Soft dependency failure produces a warning artifact for downstream."""

    def test_soft_failed_produces_warning_artifact(self):
        upstream = _make_node("quality", status=NodeStatus.FAILED, error="quality check failed")
        downstream = _make_node("report", agent_type="generator")
        dag = _make_dag(
            {"quality": upstream, "report": downstream},
            [DAGEdge(from_node="quality", to_node="report",
                     dependency_type=DependencyType.SOFT)],
        )
        engine = _make_engine()
        artifacts = engine._collect_input_artifacts(dag, "report")
        warnings = [a for a in artifacts
                    if a.metadata.get("type") == "soft_dependency_warning"]
        assert len(warnings) == 1
        assert "quality" in warnings[0].content
        assert "failed" in warnings[0].content.lower()

    def test_hard_failed_no_warning_artifact(self):
        """Hard dependency failure doesn't produce warning (node is skipped)."""
        upstream = _make_node("gen", status=NodeStatus.FAILED, error="boom")
        downstream = _make_node("eval", agent_type="evaluator")
        dag = _make_dag(
            {"gen": upstream, "eval": downstream},
            [DAGEdge(from_node="gen", to_node="eval",
                     dependency_type=DependencyType.HARD)],
        )
        engine = _make_engine()
        artifacts = engine._collect_input_artifacts(dag, "eval")
        warnings = [a for a in artifacts
                    if a.metadata.get("type") == "soft_dependency_warning"]
        assert len(warnings) == 0

    def test_success_produces_normal_artifact(self):
        upstream = _make_node("gen", status=NodeStatus.SUCCESS,
                              result={"summary": "done"}, output_artifacts=["a.py"])
        downstream = _make_node("eval", agent_type="evaluator")
        dag = _make_dag(
            {"gen": upstream, "eval": downstream},
            [DAGEdge(from_node="gen", to_node="eval",
                     dependency_type=DependencyType.SOFT)],
        )
        engine = _make_engine()
        artifacts = engine._collect_input_artifacts(dag, "eval")
        # Should have a normal handoff artifact, not a warning
        normal = [a for a in artifacts if a.from_agent == "generator"]
        assert len(normal) == 1
        warnings = [a for a in artifacts
                    if a.metadata.get("type") == "soft_dependency_warning"]
        assert len(warnings) == 0


# =====================================================================
# Independent branches with mixed dependency types
# =====================================================================

class TestBranchIndependence:
    """Branch isolation with dependency types."""

    @pytest.mark.asyncio
    async def test_soft_dep_in_other_branch_doesnt_affect_this(self):
        """A soft dep failure in another branch doesn't affect this branch."""
        # Branch A: gen_a (FAILED, hard dep to eval_a)
        # Branch B: gen_b (independent)
        gen_a = _make_node("gen_a", status=NodeStatus.FAILED, error="boom")
        gen_b = _make_node("gen_b")
        eval_a = _make_node("eval_a", agent_type="evaluator")
        dag = _make_dag(
            {"gen_a": gen_a, "gen_b": gen_b, "eval_a": eval_a},
            [DAGEdge(from_node="gen_a", to_node="eval_a",
                     dependency_type=DependencyType.HARD)],
        )
        engine = _make_engine()
        await engine._execute_single_node(dag, "gen_b")
        assert dag.nodes["gen_b"].status != NodeStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_independent_node_not_affected_by_soft_failure(self):
        """A node with no edges to the failed node is unaffected."""
        quality = _make_node("quality", status=NodeStatus.FAILED, error="fail")
        report = _make_node("report")
        unrelated = _make_node("unrelated")
        dag = _make_dag(
            {"quality": quality, "report": report, "unrelated": unrelated},
            [DAGEdge(from_node="quality", to_node="report",
                     dependency_type=DependencyType.SOFT)],
        )
        engine = _make_engine()
        await engine._execute_single_node(dag, "unrelated")
        assert dag.nodes["unrelated"].status != NodeStatus.SKIPPED
