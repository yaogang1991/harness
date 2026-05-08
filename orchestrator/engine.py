"""
Orchestrator: workflow engine that coordinates Planner, Generator, and Evaluator.
This is the core of the unattended harness.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import yaml

from core.models import (
    WorkflowDefinition,
    WorkflowStage,
    SessionState,
    GuardrailPolicy,
    PermissionMode,
    EventType,
)
from core.config import HarnessConfig
from session.store import SessionStore
from agent.worker import AgentWorker
from tools.registry import ToolRegistry
from guardrails.policy import Guardrails
from evaluator.engine import EvaluatorEngine


class Orchestrator:
    """
    Main orchestration engine for unattended software development.
    Manages the full lifecycle: REQUIREMENT → DESIGN → CODING → TESTING → REVIEW → DELIVERY
    """

    SYSTEM_PROMPTS = {
        "planner": """You are a software architect and product planner. Your role is to:
1. Analyze requirements and produce detailed design documents
2. Break down work into actionable stages
3. Define clear success criteria for each stage
4. Focus on WHAT and WHY, not HOW (implementation details)

You have access to tools: read, write, glob, grep.
Produce structured artifacts (Markdown specs, task lists).
""",
        "generator": """You are a senior software engineer. Your role is to:
1. Implement features according to specifications
2. Write clean, tested, documented code
3. Follow existing project conventions
4. Run tests and fix issues iteratively

You have access to tools: read, write, edit, bash, glob, grep, git.
Use edit for precise changes (old_string → new_string pattern).
Always verify your work by running tests.
""",
        "evaluator": """You are a QA engineer and code reviewer. Your role is to:
1. Verify code meets the sprint contract criteria
2. Run automated tests and report results
3. Check code quality (lint, coverage, complexity)
4. Provide specific, actionable feedback

You have access to tools: read, bash, glob, grep.
Be strict but constructive. Pass/Fail decisions must be based on objective criteria.
""",
    }

    def __init__(self, config: HarnessConfig):
        self.config = config
        self.session_store = SessionStore(config.event_store_path)
        self.tool_registry = ToolRegistry()
        self.evaluator = EvaluatorEngine(self.session_store)
        self.agent = AgentWorker(config.llm, self.session_store)

    def run_workflow(self, workflow_path: str, requirement: str) -> str:
        """
        Run a complete unattended workflow.
        Returns the session ID for tracking.
        """
        # Load workflow definition
        workflow = self._load_workflow(workflow_path)
        
        # Create session
        session_id = str(uuid.uuid4())
        state = self.session_store.create_session(session_id, workflow.name)
        
        # Emit requirement as first event
        self.session_store.emit_event(
            session_id,
            EventType.USER_MESSAGE,
            {"content": requirement, "type": "requirement"},
        )

        # Initialize guardrails
        policy = GuardrailPolicy(
            mode=PermissionMode.ACCEPT_EDITS,
            auto_approve_read=True,
            max_iterations=50,
        )
        guardrails = Guardrails(policy, self.tool_registry)

        # Run stages
        for stage in workflow.stages:
            should_continue = self._run_stage(
                session_id, state, stage, requirement, guardrails
            )
            if not should_continue:
                break

        # Finalize
        self.session_store.emit_event(
            session_id,
            EventType.SESSION_END,
            {"stages_completed": state.stages_completed},
        )

        return session_id

    def _run_stage(
        self,
        session_id: str,
        state: SessionState,
        stage: WorkflowStage,
        requirement: str,
        guardrails: Guardrails,
    ) -> bool:
        """Run a single workflow stage. Returns False if workflow should stop."""
        
        self.session_store.emit_event(
            session_id,
            EventType.WORKFLOW_STAGE_START,
            {"stage_name": stage.name, "agent": stage.agent},
        )

        # Build system prompt and user message for this stage
        system_prompt = self.SYSTEM_PROMPTS.get(stage.agent, self.SYSTEM_PROMPTS["generator"])
        
        # Load input artifacts
        context = f"Original requirement:\n{requirement}\n\n"
        for artifact_name in stage.input_artifacts:
            artifact_path = Path(self.config.artifact_path) / session_id / artifact_name
            if artifact_path.exists():
                content = artifact_path.read_text()
                context += f"\n--- {artifact_name} ---\n{content}\n"

        # Run agent loop
        iteration = 0
        errors = []
        
        for msg in self.agent.run(
            session_id=session_id,
            system_prompt=system_prompt,
            user_message=context + f"\n\nCurrent stage: {stage.name}\nOutput artifacts: {stage.output_artifacts}",
            tools=self._build_tool_schemas(guardrails),
            tool_executor=self.tool_registry,
            max_iterations=stage.max_iterations,
        ):
            iteration += 1
            
            # Check session limits
            ok, reason = guardrails.check_session_limits(iteration, errors)
            if not ok:
                self.session_store.emit_event(
                    session_id,
                    EventType.WORKFLOW_STAGE_ERROR,
                    {"stage": stage.name, "error": reason},
                )
                return False

        # Evaluate if criteria defined
        if stage.success_criteria:
            artifact_dir = Path(self.config.artifact_path) / session_id
            eval_result = self.evaluator.evaluate_stage(
                session_id,
                stage.name,
                stage.success_criteria,
                str(artifact_dir),
            )
            
            if not eval_result.passed:
                # Retry logic: if failed and iterations left, retry
                if iteration < stage.max_iterations:
                    # Emit feedback and retry
                    self.session_store.emit_event(
                        session_id,
                        EventType.AGENT_MESSAGE,
                        {
                            "role": "user",
                            "content": f"Evaluation failed. Feedback:\n{eval_result.feedback}\nPlease fix the issues.",
                        },
                    )
                    # In a full implementation, we would retry here
                else:
                    self.session_store.emit_event(
                        session_id,
                        EventType.WORKFLOW_STAGE_ERROR,
                        {
                            "stage": stage.name,
                            "error": f"Stage failed evaluation: {eval_result.feedback}",
                        },
                    )
                    if guardrails.policy.require_human_on_error:
                        return False

        # Stage complete
        self.session_store.emit_event(
            session_id,
            EventType.WORKFLOW_STAGE_END,
            {"stage_name": stage.name, "success": True},
        )
        
        state.stages_completed.append(stage.name)
        return True

    def _load_workflow(self, path: str) -> WorkflowDefinition:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return WorkflowDefinition(**data)

    def _build_tool_schemas(self, guardrails: Guardrails) -> list[dict]:
        """Build tool schemas, filtering by guardrails policy."""
        all_schemas = self.tool_registry.schemas
        
        if guardrails.policy.mode == PermissionMode.DONT_ASK:
            allowed = set(guardrails.policy.allowed_tools)
            return [s for s in all_schemas if s["name"] in allowed]
        
        # For other modes, include all but enforce at execution time
        return all_schemas

    def get_session_report(self, session_id: str) -> dict[str, Any]:
        """Generate a human-readable session report."""
        events = self.session_store.get_events(session_id)
        state = self.session_store.restore_state(session_id)
        
        report = {
            "session_id": session_id,
            "status": state.status,
            "stages_completed": state.stages_completed,
            "total_events": len(events),
            "total_tool_calls": state.metrics.total_tool_calls,
            "errors": state.metrics.errors,
            "artifacts": state.artifacts,
        }
        
        return report
