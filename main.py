#!/usr/bin/env python3
"""
Harness CLI Entry Point: Intelligent Multi-Agent Orchestration.

Usage:
    python main.py plan "Build a REST API for user authentication"
    python main.py execute ./data/plans/plan_xxx.json
    python main.py run "Add OAuth2 support" --project ./my-project
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.config import HarnessConfig, LLMConfig
from core.agent_registry import AgentRegistry
from core.models_v2 import DAG
from core.dag_engine import DAGExecutionEngine
from orchestrator.intelligent_orchestrator import IntelligentOrchestrator
from agent.agent_pool import AgentPool
from session.store import SessionStore
from tools.registry import ToolRegistry


def load_registry(project_path: str | None = None) -> AgentRegistry:
    """Load agent registry with defaults + project custom agents."""
    registry = AgentRegistry()
    
    # Load project-specific agents if .harness/agents.yaml exists
    if project_path:
        agents_yaml = Path(project_path) / ".harness" / "agents.yaml"
        if agents_yaml.exists():
            print(f"Loading project agents from {agents_yaml}")
            registry.load_from_yaml(agents_yaml)
    
    return registry


async def cmd_plan(args):
    """Generate an execution plan (DAG) from requirements."""
    config = HarnessConfig.from_env()
    store = SessionStore(config.event_store_path)
    registry = load_registry(args.project)
    
    orchestrator = IntelligentOrchestrator(
        llm_config=config.llm,
        session_store=store,
        agent_registry=registry,
    )
    
    print(f"Planning: {args.requirement}")
    print(f"Available agents: {[a.id for a in registry.list_agents()]}")
    
    # Generate DAG
    dag = await orchestrator.plan(
        requirement=args.requirement,
        project_context={"project_path": args.project} if args.project else None,
    )
    
    # Save plan
    plans_dir = Path("./data/plans")
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_file = plans_dir / f"plan_{id(dag)}.json"
    
    with open(plan_file, "w") as f:
        plan_data = {
            "reasoning": dag.reasoning,
            "nodes": [
                {
                    "id": n.id,
                    "agent_type": n.agent_type,
                    "task": n.task_description,
                }
                for n in dag.nodes.values()
            ],
            "edges": [{"from": e.from_node, "to": e.to_node} for e in dag.edges],
            "levels": dag.topological_levels(),
        }
        json.dump(plan_data, f, indent=2, default=str)
    
    # Print plan summary
    print(f"\nPlan saved: {plan_file}")
    print(f"\nReasoning: {dag.reasoning}")
    print(f"\nExecution levels:")
    for i, level in enumerate(dag.topological_levels()):
        print(f"  Level {i}: {' → '.join(level)}")
    
    return dag


async def cmd_execute(args):
    """Execute a saved plan (DAG)."""
    config = HarnessConfig.from_env()
    store = SessionStore(config.event_store_path)
    registry = load_registry(args.project)
    
    # Create session
    session_id = str(uuid.uuid4())
    store.create_session(session_id, "harness_run")
    
    # Load DAG
    with open(args.plan_file, "r") as f:
        plan_data = json.load(f)
    
    dag = DAG()
    for node_def in plan_data["nodes"]:
        from core.models_v2 import DAGNode
        dag.add_node(DAGNode(
            id=node_def["id"],
            agent_type=node_def["agent_type"],
            task_description=node_def["task"],
        ))
    for edge_def in plan_data.get("edges", []):
        dag.add_edge(edge_def["from"], edge_def["to"])
    
    # Create agent pool with tool registry
    pool = AgentPool(config.llm, store, registry, ToolRegistry())
    
    # Create orchestrator for failure handling
    orchestrator = IntelligentOrchestrator(config.llm, store, registry)
    
    # Create DAG engine
    engine = DAGExecutionEngine(
        agent_executor=pool.get_executor(session_id),
        failure_handler=orchestrator.adapt_to_failure,
        max_parallel=args.max_parallel,
    )
    
    # Progress callback
    async def on_event(event):
        print(f"  [{event.event_type.upper()}] {event.node_id}: {event.details}")
    
    engine.on_event(on_event)
    
    print(f"Executing DAG with {len(dag.nodes)} nodes...")
    print(f"Levels: {dag.topological_levels()}")
    print()
    
    # Execute
    result_dag = await engine.execute(dag)
    
    # Summary
    summary = engine.get_execution_summary(result_dag)
    print(f"\nExecution complete:")
    print(f"  Total: {summary['total_nodes']}")
    print(f"  Success: {summary['success']}")
    print(f"  Failed: {summary['failed']}")
    print(f"  Skipped: {summary['skipped']}")
    print(f"  Session ID: {session_id}")
    
    return result_dag


async def cmd_run(args):
    """Plan + Execute in one command."""
    # Plan
    dag = await cmd_plan(args)
    
    # Execute
    # Save to temp file for execute command
    tmp_file = Path("./data/plans/_tmp_plan.json")
    tmp_file.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_file, "w") as f:
        plan_data = {
            "reasoning": dag.reasoning,
            "nodes": [
                {"id": n.id, "agent_type": n.agent_type, "task": n.task_description}
                for n in dag.nodes.values()
            ],
            "edges": [{"from": e.from_node, "to": e.to_node} for e in dag.edges],
        }
        json.dump(plan_data, f, indent=2, default=str)
    
    # Create args for execute
    exec_args = argparse.Namespace(
        plan_file=str(tmp_file),
        project=args.project,
        max_parallel=args.max_parallel,
    )
    return await cmd_execute(exec_args)


def main():
    parser = argparse.ArgumentParser(
        description="Harness - Intelligent Multi-Agent Orchestration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate execution plan
  python main.py plan "Build a REST API for user authentication"

  # Execute a saved plan
  python main.py execute ./data/plans/plan_123.json

  # Plan + Execute in one step
  python main.py run "Add OAuth2 support" --project ./my-project

  # Use project-specific agents
  python main.py run "Design UI" --project ./my-project
        """,
    )
    
    parser.add_argument(
        "--project",
        help="Path to project directory (loads .harness/agents.yaml if exists)",
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=3,
        help="Max parallel agent executions (default: 3)",
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command")
    
    # plan command
    plan_parser = subparsers.add_parser("plan", help="Generate execution plan")
    plan_parser.add_argument("requirement", help="User requirement")
    plan_parser.set_defaults(func=cmd_plan)
    
    # execute command
    exec_parser = subparsers.add_parser("execute", help="Execute a saved plan")
    exec_parser.add_argument("plan_file", help="Path to plan JSON file")
    exec_parser.set_defaults(func=cmd_execute)
    
    # run command (plan + execute)
    run_parser = subparsers.add_parser("run", help="Plan and execute in one step")
    run_parser.add_argument("requirement", help="User requirement")
    run_parser.set_defaults(func=cmd_run)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Ensure API key
    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        print("Error: ANTHROPIC_API_KEY or OPENAI_API_KEY must be set")
        sys.exit(1)
    
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
