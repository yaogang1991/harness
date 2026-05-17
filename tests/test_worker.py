"""
Tests for agent/worker.py — AgentWorker loop, context management, retry, artifact tracking.
"""
import pytest
from unittest.mock import MagicMock, call  # noqa: F401

from core.models import ToolResult
from agent.worker import AgentWorker


@pytest.fixture
def worker(tmp_store, llm_config):
    return AgentWorker(llm_config, tmp_store, max_context_tokens=1000)


class TestBasicLoop:
    def test_text_only_response(self, worker, mock_tool_executor):
        """Worker yields one message and stops when no tool calls."""
        worker.llm.call = MagicMock(return_value={
            "role": "assistant", "content": "Done",
        })
        msgs = list(worker.run("s1", "sys", "do it", [], mock_tool_executor))
        assert len(msgs) == 1
        assert msgs[0].content == "Done"
        assert msgs[0].tool_calls == []

    def test_tool_then_text(self, worker, mock_tool_executor):
        """Worker executes tool calls then stops on text response."""
        worker.llm.call = MagicMock(side_effect=[
            {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "tc1", "name": "read", "arguments": {"file_path": "x.py"}}],
            },
            {"role": "assistant", "content": "Finished"},
        ])
        msgs = list(worker.run("s1", "sys", "do it", [{"name": "read"}], mock_tool_executor))
        assert len(msgs) == 2
        assert msgs[0].tool_calls is not None
        assert msgs[1].content == "Finished"
        mock_tool_executor.execute.assert_called_once_with("read", {"file_path": "x.py"})

    def test_max_iterations(self, worker, mock_tool_executor):
        """Loop stops at max_iterations even with continuous tool calls."""
        worker.llm.call = MagicMock(return_value={
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "tc1", "name": "bash", "arguments": {"command": "ls"}}],
        })
        msgs = list(worker.run("s1", "sys", "do it", [], mock_tool_executor, max_iterations=3))
        assert len(msgs) == 3


class TestContextManagement:
    def test_estimate_tokens(self):
        msgs = [{"role": "user", "content": "a" * 400}]
        assert AgentWorker._estimate_tokens(msgs) == 100

    def test_estimate_tokens_includes_tool_args(self):
        msgs = [{"role": "assistant", "content": "", "tool_calls": [
            {"arguments": {"key": "v" * 400}}
        ]}]
        # 400 chars from args, ~100 tokens
        assert AgentWorker._estimate_tokens(msgs) >= 90

    def test_truncate_preserves_system(self, worker):
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(30):
            msgs.append({"role": "user", "content": f"msg {i} " * 200})
        result = worker._truncate_messages(msgs, 1000)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "sys"
        assert len(result) < len(msgs)

    def test_truncate_noop_under_limit(self, worker):
        msgs = [{"role": "system", "content": "hi"}, {"role": "user", "content": "hello"}]
        result = worker._truncate_messages(msgs, 10000)
        assert result == msgs

    def test_estimate_tokens_cjk_content(self):
        """CJK characters use ~2 chars/token, not 4 (#479)."""
        cjk_text = "你" * 400  # 400 CJK characters
        msgs = [{"role": "user", "content": cjk_text}]
        tokens = AgentWorker._estimate_tokens(msgs)
        # CJK: 400 // 2 = 200 tokens
        assert tokens == 200

    def test_estimate_tokens_mixed_cjk_and_english(self):
        """Mixed content: CJK + English each estimated correctly (#479)."""
        msgs = [{"role": "user", "content": "你好" * 50 + "a" * 200}]
        tokens = AgentWorker._estimate_tokens(msgs)
        # CJK: 100 // 2 = 50, English: 200 // 4 = 50, total = 100
        assert tokens == 100

    def test_estimate_tokens_cjk_tool_args(self):
        """CJK in tool call arguments is also estimated correctly (#479)."""
        msgs = [{"role": "assistant", "content": "", "tool_calls": [
            {"arguments": {"key": "代码" * 100}}
        ]}]
        tokens = AgentWorker._estimate_tokens(msgs)
        # 200 CJK chars + a few ASCII from dict str(); well above pure ASCII
        # estimate of ~8 tokens for same-length dict representation
        assert tokens >= 90


class TestArtifactTracking:
    def test_tracks_write(self, worker, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x = 1\n", encoding="utf-8")
        mock_exec = MagicMock()
        mock_exec.execute = MagicMock(return_value=ToolResult(
            tool_call_id="tc1", success=True, output="ok",
        ))
        worker.llm.call = MagicMock(side_effect=[
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "tc1", "name": "write",
                             "arguments": {"file_path": str(f),
                                           "content": "x = 1"}}]},
            {"role": "assistant", "content": "done"},
        ])
        list(worker.run("s1", "sys", "do it", [], mock_exec))
        assert worker.artifacts == [str(f)]

    def test_tracks_edit(self, worker, tmp_path):
        f = tmp_path / "b.py"
        f.write_text("old\n", encoding="utf-8")
        mock_exec = MagicMock()
        mock_exec.execute = MagicMock(return_value=ToolResult(
            tool_call_id="tc1", success=True, output="ok",
        ))
        worker.llm.call = MagicMock(side_effect=[
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "tc1", "name": "edit",
                             "arguments": {"file_path": str(f),
                                           "old_string": "old",
                                           "new_string": "new"}}]},
            {"role": "assistant", "content": "done"},
        ])
        list(worker.run("s1", "sys", "do it", [], mock_exec))
        assert worker.artifacts == [str(f)]

    def test_no_duplicates(self, worker, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("x = 1\n", encoding="utf-8")
        mock_exec = MagicMock()
        mock_exec.execute = MagicMock(return_value=ToolResult(
            tool_call_id="tc1", success=True, output="ok",
        ))
        worker.llm.call = MagicMock(side_effect=[
            {"role": "assistant", "content": "",
             "tool_calls": [
                 {"id": "tc1", "name": "write",
                  "arguments": {"file_path": str(f),
                                "content": "x = 1"}},
                 {"id": "tc2", "name": "edit",
                  "arguments": {"file_path": str(f),
                                "old_string": "x = 1",
                                "new_string": "x = 2"}},
             ]},
            {"role": "assistant", "content": "done"},
        ])
        list(worker.run("s1", "sys", "do it", [], mock_exec))
        assert worker.artifacts == [str(f)]

    def test_resets_on_new_run(self, worker, mock_tool_executor):
        worker.artifacts = ["/old/file.py"]
        worker.llm.call = MagicMock(return_value={"role": "assistant", "content": "ok"})
        list(worker.run("s1", "sys", "do it", [], mock_tool_executor))
        assert worker.artifacts == []

    def test_failed_tool_not_tracked(self, worker):
        mock_exec = MagicMock()
        mock_exec.execute = MagicMock(return_value=ToolResult(
            tool_call_id="tc1", success=False, error="denied",
        ))
        worker.llm.call = MagicMock(side_effect=[
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "tc1", "name": "write", "arguments": {"file_path": "no.py"}}]},
            {"role": "assistant", "content": "done"},
        ])
        list(worker.run("s1", "sys", "do it", [], mock_exec))
        assert worker.artifacts == []
