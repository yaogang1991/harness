"""
Shared test fixtures.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure project root is on sys.path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import LLMConfig
from core.models import ToolResult
from session.store import SessionStore


@pytest.fixture
def tmp_store(tmp_path):
    """SessionStore backed by a temporary directory."""
    return SessionStore(str(tmp_path / "events"))


@pytest.fixture
def llm_config():
    return LLMConfig(api_key="test-key", model="test-model")


@pytest.fixture
def mock_llm_client():
    """Mock LLMClient that returns a configurable sequence of responses."""
    client = MagicMock()
    client.call = MagicMock(return_value={
        "role": "assistant",
        "content": "Task completed",
    })
    return client


@pytest.fixture
def mock_tool_executor():
    """Mock tool executor that returns success."""
    executor = MagicMock()
    executor.execute = MagicMock(return_value=ToolResult(
        tool_call_id="test-id",
        success=True,
        output="Tool output",
    ))
    return executor
