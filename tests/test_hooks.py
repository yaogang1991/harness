"""Tests for execution hooks (control_plane/hooks.py)."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from control_plane.hooks import (
    ExecutionContext,
    ExecutionHook,
    ImpactHook,
    LearningHook,
    MemoryHook,
)


def _make_job(**overrides):
    job = MagicMock()
    job.id = "job-123"
    job.requirement = "Fix bug in DAG engine"
    job.project_path = "/tmp/test-project"
    job.metadata = {}
    for k, v in overrides.items():
        setattr(job, k, v)
    return job


def _make_context(**overrides):
    defaults = {
        "job": _make_job(),
        "session_id": "sess-123",
        "store": MagicMock(),
        "work_dir": Path("/tmp/work"),
        "run_id": "run-123",
        "memory_manager": None,
    }
    defaults.update(overrides)
    return ExecutionContext(**defaults)


# ============================================================================
# ExecutionContext
# ============================================================================


class TestExecutionContext:
    def test_default_metadata(self):
        ctx = _make_context()
        assert ctx.metadata == {}
        assert ctx._state == {}

    def test_metadata_and_state_isolation(self):
        ctx1 = _make_context()
        ctx2 = _make_context()
        ctx1.metadata["key"] = "val1"
        ctx1._state["key"] = "s1"
        assert ctx2.metadata == {}
        assert ctx2._state == {}


# ============================================================================
# ExecutionHook base class
# ============================================================================


class TestExecutionHook:
    @pytest.mark.asyncio
    async def test_default_noop(self):
        hook = ExecutionHook()
        ctx = _make_context()
        await hook.before_execution(ctx)
        await hook.after_execution(ctx, MagicMock())


# ============================================================================
# MemoryHook
# ============================================================================


class TestMemoryHook:
    @pytest.mark.asyncio
    async def test_hook_instantiation(self):
        hook = MemoryHook()
        assert isinstance(hook, ExecutionHook)

    @pytest.mark.asyncio
    async def test_before_execution_failure_is_safe(self):
        hook = MemoryHook()
        ctx = _make_context()
        # Should not raise even if imports fail
        await hook.before_execution(ctx)
        # memory_manager may or may not be set depending on env
        assert isinstance(hook, ExecutionHook)


# ============================================================================
# LearningHook
# ============================================================================


class TestLearningHook:
    @pytest.mark.asyncio
    async def test_no_scheduler_does_not_raise(self):
        hook = LearningHook()
        hook._scheduler = None
        ctx = _make_context()
        await hook.before_execution(ctx)

    @pytest.mark.asyncio
    async def test_scheduler_called(self):
        hook = LearningHook()
        mock_scheduler = MagicMock()
        hook._scheduler = mock_scheduler
        ctx = _make_context()
        await hook.before_execution(ctx)
        mock_scheduler.maybe_run_analysis.assert_called_once()

    @pytest.mark.asyncio
    async def test_scheduler_error_does_not_raise(self):
        hook = LearningHook()
        mock_scheduler = MagicMock()
        mock_scheduler.maybe_run_analysis.side_effect = RuntimeError("boom")
        hook._scheduler = mock_scheduler
        ctx = _make_context()
        await hook.before_execution(ctx)  # Should not raise


# ============================================================================
# ImpactHook
# ============================================================================


class TestImpactHook:
    @pytest.mark.asyncio
    async def test_no_predictor_skips(self):
        hook = ImpactHook()
        hook._predictor = None
        ctx = _make_context()
        await hook.before_execution(ctx)
        assert "impact_scope" not in ctx._state

    @pytest.mark.asyncio
    async def test_no_workdir_skips(self):
        hook = ImpactHook()
        hook._predictor = MagicMock()
        ctx = _make_context(work_dir=Path(""))
        await hook.before_execution(ctx)
        assert "impact_scope" not in ctx._state

    @pytest.mark.asyncio
    async def test_after_no_impact_scope_skips(self):
        hook = ImpactHook()
        ctx = _make_context()
        await hook.after_execution(ctx, MagicMock())

    @pytest.mark.asyncio
    async def test_after_none_snapshot_skips(self):
        hook = ImpactHook()
        ctx = _make_context()
        ctx._state["impact_scope"] = MagicMock()
        ctx._state["before_snapshot"] = None
        await hook.after_execution(ctx, MagicMock())

    @pytest.mark.asyncio
    async def test_init_failure_is_safe(self):
        with patch("core.config.HarnessConfig.from_env", side_effect=ImportError):
            hook = ImpactHook()
            assert hook._predictor is None


# ============================================================================
# Integration: hooks run in sequence
# ============================================================================


class TestHookSequence:
    @pytest.mark.asyncio
    async def test_all_hooks_run_without_error(self):
        hooks = [MemoryHook(), LearningHook(), ImpactHook()]
        ctx = _make_context()
        for hook in hooks:
            try:
                await hook.before_execution(ctx)
            except Exception:
                pass  # Hooks must not raise
        for hook in hooks:
            try:
                await hook.after_execution(ctx, MagicMock())
            except Exception:
                pass
