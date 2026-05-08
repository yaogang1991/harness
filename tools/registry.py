"""
Tool Registry: built-in tools + MCP integration.
All tools share a unified interface: execute(name, input) -> ToolResult
"""

import json
import subprocess
import time
from pathlib import Path
from typing import Callable

from core.models import ToolResult


class ToolRegistry:
    """
    Registry for all available tools.
    Built-in tools: read, write, edit, bash, glob, grep, git
    MCP tools: dynamically loaded from MCP servers
    """

    def __init__(self, sandbox_runner=None):
        self._tools: dict[str, Callable] = {}
        self._schemas: list[dict] = []
        self.sandbox_runner = sandbox_runner
        self._register_builtin_tools()

    def _register_builtin_tools(self):
        self.register("read", self._tool_read, {
            "name": "read",
            "description": "Read file contents. Supports text, images, PDFs.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to file"},
                    "offset": {"type": "integer", "description": "Start line (optional)"},
                    "limit": {"type": "integer", "description": "Max lines (optional)"},
                },
                "required": ["file_path"],
            },
        })

        self.register("write", self._tool_write, {
            "name": "write",
            "description": "Create or overwrite a file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
            },
        })

        self.register("edit", self._tool_edit, {
            "name": "edit",
            "description": "Replace old_string with new_string in a file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        })

        self.register("bash", self._tool_bash, {
            "name": "bash",
            "description": "Execute a bash command in the sandbox.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command to execute"},
                    "timeout": {"type": "integer", "default": 120},
                },
                "required": ["command"],
            },
        })

        self.register("glob", self._tool_glob, {
            "name": "glob",
            "description": "Find files matching a pattern.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern like '*.py'"},
                    "path": {"type": "string", "description": "Base directory"},
                },
                "required": ["pattern"],
            },
        })

        self.register("grep", self._tool_grep, {
            "name": "grep",
            "description": "Search for text in files.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "file_pattern": {"type": "string", "description": "e.g. '*.py'"},
                },
                "required": ["pattern"],
            },
        })

        self.register("git", self._tool_git, {
            "name": "git",
            "description": "Execute git commands (status, diff, commit, branch, etc.)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Git subcommand"},
                    "args": {"type": "array", "items": {"type": "string"}, "default": []},
                },
                "required": ["command"],
            },
        })

    def register(self, name: str, handler: Callable, schema: dict):
        self._tools[name] = handler
        self._schemas.append(schema)

    def get_schema(self, name: str) -> dict | None:
        for schema in self._schemas:
            if schema["name"] == name:
                return schema
        return None

    @property
    def schemas(self) -> list[dict]:
        return self._schemas

    def execute(self, name: str, arguments: dict) -> ToolResult:
        if name not in self._tools:
            return ToolResult(
                tool_call_id="",
                success=False,
                error=f"Tool '{name}' not found",
            )

        start = time.time()
        try:
            result = self._tools[name](**arguments)
            duration = int((time.time() - start) * 1000)
            if isinstance(result, ToolResult):
                result.duration_ms = duration
                return result
            return ToolResult(
                tool_call_id="",
                success=True,
                output=str(result),
                duration_ms=duration,
            )
        except Exception as e:
            return ToolResult(
                tool_call_id="",
                success=False,
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    # --- Built-in tool implementations ---

    def _tool_read(self, file_path: str, offset: int = 0, limit: int = 1000) -> ToolResult:
        try:
            path = Path(file_path)
            if not path.exists():
                return ToolResult(tool_call_id="", success=False, error=f"File not found: {file_path}")
            
            with open(path, "r") as f:
                lines = f.readlines()
            
            selected = lines[offset:offset + limit]
            content = "".join(selected)
            
            return ToolResult(tool_call_id="", success=True, output=content)
        except Exception as e:
            return ToolResult(tool_call_id="", success=False, error=str(e))

    def _tool_write(self, file_path: str, content: str) -> ToolResult:
        try:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return ToolResult(tool_call_id="", success=True, output=f"Written {len(content)} chars to {file_path}")
        except Exception as e:
            return ToolResult(tool_call_id="", success=False, error=str(e))

    def _tool_edit(self, file_path: str, old_string: str, new_string: str) -> ToolResult:
        try:
            path = Path(file_path)
            if not path.exists():
                return ToolResult(tool_call_id="", success=False, error=f"File not found: {file_path}")
            
            content = path.read_text()
            if old_string not in content:
                return ToolResult(tool_call_id="", success=False, error=f"old_string not found in file")
            
            content = content.replace(old_string, new_string, 1)
            path.write_text(content)
            return ToolResult(tool_call_id="", success=True, output=f"Edited {file_path}")
        except Exception as e:
            return ToolResult(tool_call_id="", success=False, error=str(e))

    def _tool_bash(self, command: str, timeout: int = 120) -> ToolResult:
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr
            return ToolResult(
                tool_call_id="",
                success=result.returncode == 0,
                output=output,
                error=result.stderr if result.returncode != 0 else "",
            )
        except subprocess.TimeoutExpired:
            return ToolResult(tool_call_id="", success=False, error=f"Command timed out after {timeout}s")
        except Exception as e:
            return ToolResult(tool_call_id="", success=False, error=str(e))

    def _tool_glob(self, pattern: str, path: str = ".") -> ToolResult:
        try:
            base = Path(path)
            matches = list(base.rglob(pattern))
            output = "\n".join(str(m.relative_to(base)) for m in matches)
            return ToolResult(tool_call_id="", success=True, output=output or "No matches")
        except Exception as e:
            return ToolResult(tool_call_id="", success=False, error=str(e))

    def _tool_grep(self, pattern: str, path: str = ".", file_pattern: str = "*") -> ToolResult:
        try:
            base = Path(path)
            matches = []
            for file_path in base.rglob(file_pattern):
                if file_path.is_file():
                    try:
                        content = file_path.read_text(errors="ignore")
                        if pattern in content:
                            lines = [f"{file_path}:{i+1}:{line}" for i, line in enumerate(content.split("\n")) if pattern in line]
                            matches.extend(lines)
                    except Exception:
                        continue
            return ToolResult(tool_call_id="", success=True, output="\n".join(matches[:50]) or "No matches")
        except Exception as e:
            return ToolResult(tool_call_id="", success=False, error=str(e))

    def _tool_git(self, command: str, args: list = None) -> ToolResult:
        args = args or []
        full_cmd = ["git", command] + args
        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr
            return ToolResult(
                tool_call_id="",
                success=result.returncode == 0,
                output=output,
                error=result.stderr if result.returncode != 0 else "",
            )
        except Exception as e:
            return ToolResult(tool_call_id="", success=False, error=str(e))
