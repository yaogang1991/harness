"""
Agent Worker: the "dumb loop" that calls the LLM and executes tools.
All intelligence lives in the model. Harness just orchestrates.
"""

import json
import os
from typing import Iterator

import anthropic
from openai import OpenAI

from core.models import AgentMessage, ToolCall, ToolResult, EventType
from core.config import LLMConfig
from session.store import SessionStore


class AgentWorker:
    """
    Minimal harness loop:
    while has_tool_calls:
        call LLM with messages
        execute tool calls
        feed results back
    """

    def __init__(self, config: LLMConfig, session_store: SessionStore):
        self.config = config
        self.session_store = session_store
        self.client = self._create_client()

    def _create_client(self):
        if self.config.provider == "anthropic":
            return anthropic.Anthropic(
                api_key=self.config.api_key,
                base_url=self.config.base_url or None,
                timeout=self.config.timeout,
            )
        else:
            return OpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url or None,
                timeout=self.config.timeout,
            )

    def run(
        self,
        session_id: str,
        system_prompt: str,
        user_message: str,
        tools: list[dict],
        tool_executor,
        max_iterations: int = 50,
    ) -> Iterator[AgentMessage]:
        """
        Run the agent loop until no more tool calls or max iterations reached.
        Yields each assistant message for streaming/real-time observation.
        """
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        for iteration in range(max_iterations):
            assistant_message = self._call_llm(messages, tools)
            
            self.session_store.emit_event(
                session_id,
                EventType.AGENT_MESSAGE,
                assistant_message,
            )

            if "tool_calls" not in assistant_message or not assistant_message["tool_calls"]:
                yield AgentMessage(role="assistant", content=assistant_message.get("content", ""))
                break

            yield AgentMessage(
                role="assistant",
                content=assistant_message.get("content", ""),
                tool_calls=[ToolCall(**tc) for tc in assistant_message["tool_calls"]],
            )

            # Execute tool calls
            tool_results = []
            for tc in assistant_message["tool_calls"]:
                self.session_store.emit_event(
                    session_id,
                    EventType.AGENT_TOOL_USE,
                    tc,
                )

                result = tool_executor.execute(tc["name"], tc.get("arguments", {}))
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result.output if result.success else f"Error: {result.error}",
                })

                self.session_store.emit_event(
                    session_id,
                    EventType.AGENT_TOOL_RESULT,
                    {
                        "tool_call_id": tc["id"],
                        "success": result.success,
                        "output": result.output,
                        "error": result.error,
                        "duration_ms": result.duration_ms,
                    },
                )

            messages.append(assistant_message)
            messages.extend(tool_results)

    def _call_llm(self, messages: list[dict], tools: list[dict]) -> dict:
        if self.config.provider == "anthropic":
            return self._call_anthropic(messages, tools)
        else:
            return self._call_openai(messages, tools)

    def _call_anthropic(self, messages: list[dict], tools: list[dict]) -> dict:
        """Call Anthropic API with proper message format conversion."""
        # Extract system prompt (Anthropic requires it as a separate parameter)
        system_prompt = None
        anthropic_messages = []

        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
                continue

            if msg.get("role") == "assistant":
                content_blocks = []
                text_content = msg.get("content", "")
                if text_content:
                    content_blocks.append({"type": "text", "text": text_content})
                for tc in msg.get("tool_calls", []):
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["arguments"],
                    })
                anthropic_messages.append({"role": "assistant", "content": content_blocks})

            elif msg.get("role") == "tool":
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg["tool_call_id"],
                        "content": msg["content"],
                    }],
                })

            else:
                anthropic_messages.append(msg)

        kwargs = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "messages": anthropic_messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = tools

        response = self.client.messages.create(**kwargs)

        msg = {
            "role": "assistant",
            "content": "",
        }

        tool_calls = []
        for block in response.content:
            if block.type == "text":
                msg["content"] += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                })

        if tool_calls:
            msg["tool_calls"] = tool_calls

        return msg

    def _call_openai(self, messages: list[dict], tools: list[dict]) -> dict:
        response = self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            tools=tools if tools else None,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )
        
        choice = response.choices[0]
        msg = {
            "role": "assistant",
            "content": choice.message.content or "",
        }
        
        if choice.message.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                }
                for tc in choice.message.tool_calls
            ]
        
        return msg
