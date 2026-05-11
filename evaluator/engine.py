"""
Evaluator: automated evaluation and contract verification.
Inspired by Anthropic's three-agent harness evaluator.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from core.models import (
    CriterionType,
    EvaluationResult,
    EventType,
    SuccessCriterion,
)
from session.store import SessionStore


class EvaluatorEngine:
    """
    Evaluates code against predefined success criteria.

    Supports both legacy list[str] criteria and structured SuccessCriterion.
    Evaluation is always scoped to work_dir (defaults to artifact_path).
    """

    def __init__(self, session_store: SessionStore):
        self.session_store = session_store

    def evaluate_stage(
        self,
        session_id: str,
        stage_name: str,
        criteria: list[str] | list[SuccessCriterion],
        artifact_path: str,
        work_dir: str | None = None,
        output_artifacts: list[str] | None = None,
    ) -> EvaluationResult:
        """Evaluate a stage against its success criteria."""
        eval_dir = work_dir or artifact_path

        self.session_store.emit_event(
            session_id,
            EventType.EVAL_START,
            {"stage": stage_name, "criteria": [str(c) for c in criteria], "artifact": artifact_path},
        )

        # Normalize to SuccessCriterion list
        structured = self._normalize_criteria(criteria)

        results: dict[str, bool] = {}
        score = 0.0
        feedback_parts: list[str] = []
        uncheckable: list[str] = []

        for crit in structured:
            passed, msg, auto = self._check_criterion(crit, eval_dir, output_artifacts)
            label = crit.description or crit.command or crit.path or str(crit.type)
            results[label] = passed
            if passed:
                score += 10.0 / max(len(structured), 1)
            if auto:
                feedback_parts.append(f"{'PASS' if passed else 'FAIL'} {label}: {msg}")
            else:
                feedback_parts.append(f"WARN {label}: {msg}")
                uncheckable.append(label)

        all_auto_passed = all(results.values())
        has_uncheckable = len(uncheckable) > 0
        overall_passed = all_auto_passed and not has_uncheckable

        feedback = "\n".join(feedback_parts)
        if has_uncheckable:
            feedback += (
                f"\n\nWARNING: {len(uncheckable)} criterion/criteria could not be "
                f"automatically verified and require manual review: "
                f"{', '.join(uncheckable)}"
            )

        result = EvaluationResult(
            passed=overall_passed,
            score=round(score, 1),
            criteria_results=results,
            feedback=feedback,
            suggestions=uncheckable,
        )

        self.session_store.emit_event(
            session_id,
            EventType.EVAL_RESULT,
            result.model_dump(),
        )

        return result

    def _normalize_criteria(
        self, criteria: list[str] | list[SuccessCriterion]
    ) -> list[SuccessCriterion]:
        """Convert legacy string criteria to SuccessCriterion."""
        result: list[SuccessCriterion] = []
        for c in criteria:
            if isinstance(c, SuccessCriterion):
                result.append(c)
            else:
                result.append(self._parse_string_criterion(c))
        return result

    def _parse_string_criterion(self, criterion: str) -> SuccessCriterion:
        """Parse a legacy string criterion into a structured SuccessCriterion."""
        lower = criterion.lower()

        if "test" in lower and "pass" in lower:
            return SuccessCriterion(
                type=CriterionType.COMMAND,
                command="python -m pytest -v --tb=short",
                description=criterion,
            )
        if "coverage" in lower:
            target = self._extract_percentage(lower) or 80
            return SuccessCriterion(
                type=CriterionType.COVERAGE,
                target=float(target),
                description=criterion,
            )
        if "lint" in lower or "clean" in lower:
            return SuccessCriterion(
                type=CriterionType.LINT,
                description=criterion,
            )
        if "file" in lower and "exist" in lower:
            match = re.search(r"[:\s]+(.+)", lower)
            path = match.group(1) if match else ""
            return SuccessCriterion(
                type=CriterionType.FILE_EXISTS,
                path=path,
                description=criterion,
            )
        if "no_critical" in lower or "no bug" in lower:
            return SuccessCriterion(
                type=CriterionType.NO_CRITICAL,
                description=criterion,
            )

        return SuccessCriterion(
            type=CriterionType.CUSTOM,
            description=criterion,
        )

    def _check_criterion(
        self,
        crit: SuccessCriterion | str,
        work_dir: str,
        output_artifacts: list[str] | None = None,
    ) -> tuple[bool, str, bool]:
        """Check a single structured criterion. Returns (passed, message, was_auto)."""
        if isinstance(crit, str):
            crit = self._parse_string_criterion(crit)
        """Check a single structured criterion. Returns (passed, message, was_auto)."""
        path = Path(work_dir)

        if crit.type == CriterionType.COMMAND:
            cmd = crit.command or "python -m pytest -v --tb=short"
            # If output_artifacts provided, scope test to those files
            if output_artifacts and "pytest" in cmd:
                files = " ".join(output_artifacts)
                cmd = f"{cmd} {files}"
            return *self._run_command(cmd, path), True

        if crit.type == CriterionType.LINT:
            lint_targets = output_artifacts or [str(path)]
            return *self._run_lint(lint_targets), True

        if crit.type == CriterionType.FILE_EXISTS:
            files_str = crit.path
            if files_str:
                files = [f.strip() for f in files_str.split(",")]
            else:
                files = output_artifacts or []
            if not files:
                return True, "No specific files listed", True
            missing = [f for f in files if not (path / f).exists()]
            passed = len(missing) == 0
            return passed, f"Missing: {missing}" if missing else "All required files present", True

        if crit.type == CriterionType.COVERAGE:
            target = int(crit.target) if crit.target else 80
            return *self._check_coverage(path, target), True

        if crit.type == CriterionType.NO_CRITICAL:
            return *self._check_no_critical(path, output_artifacts), True

        # CUSTOM — not auto-checkable
        return False, (
            f"Criterion '{crit.description}' is not automatically checkable. "
            f"Supported types: command, lint, file_exists, coverage, no_critical"
        ), False

    def _run_command(self, command: str, cwd: Path) -> tuple[bool, str]:
        """Run a shell command and return (passed, message)."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                cwd=str(cwd) if cwd.is_dir() else None,
            )
            passed = result.returncode == 0
            return passed, "Command passed" if passed else f"Command failed:\n{result.stdout[-500:]}"
        except Exception as e:
            return False, f"Command execution error: {e}"

    def _run_lint(self, targets: list[str] | Path) -> tuple[bool, str]:
        """Run lint on specified targets. Accepts Path for backward compat."""
        if isinstance(targets, Path):
            targets = [str(targets)]
        for target in targets:
            try:
                result = subprocess.run(
                    ["python", "-m", "flake8", target, "--max-line-length=100"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                passed = result.returncode == 0
                if not passed:
                    return False, f"Lint issues in {target}:\n{result.stdout[:500]}"
            except FileNotFoundError:
                try:
                    result = subprocess.run(
                        ["ruff", "check", target],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=60,
                    )
                    passed = result.returncode == 0
                    if not passed:
                        return False, f"Ruff issues in {target}:\n{result.stdout[:500]}"
                except FileNotFoundError:
                    return False, "No linter available (install flake8 or ruff)"
            except Exception as e:
                return False, f"Lint error: {e}"
        return True, "Lint clean"

    def _check_coverage(self, path: Path, target: int) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", str(path), "--cov=.", "--cov-report=term-missing"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            for line in result.stdout.split("\n"):
                if "TOTAL" in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        cov_str = parts[-1].replace("%", "")
                        try:
                            cov = float(cov_str)
                            passed = cov >= target
                            return passed, f"Coverage: {cov}% (target: {target}%)"
                        except ValueError:
                            continue
            return False, "Could not parse coverage report"
        except Exception as e:
            return False, f"Coverage check error: {e}"

    def _check_no_critical_issues(
        self, path: Path, artifacts: list[str] | None = None
    ) -> tuple[bool, str]:
        targets = artifacts if artifacts else []
        if not targets:
            return True, "No artifacts to check", True
        issues = []
        for fname in targets:
            fpath = path / fname
            if not fpath.exists():
                continue
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
                for marker in ["TODO", "FIXME", "XXX", "HACK"]:
                    if marker in content:
                        issues.append(f"{fname}: {marker}")
            except Exception:
                pass
        passed = len(issues) == 0
        return passed, f"Found markers: {issues}" if issues else "No critical markers found"

    def _extract_percentage(self, text: str) -> int | None:
        match = re.search(r'(\d+)%', text)
        if match:
            return int(match.group(1))
        return None

    # Legacy backward-compatible methods for direct test access

    def _run_tests(self, path: Path) -> tuple[bool, str]:
        """Legacy: run pytest against path."""
        return self._run_command(
            f"python -m pytest {path} -v --tb=short",
            path if path.is_dir() else Path("."),
        )

    def _check_files_exist(self, criterion: str, path: Path) -> tuple[bool, str]:
        """Legacy: check files from string criterion."""
        match = re.search(r"[:\s]+(.+)", criterion)
        if match:
            files = [f.strip() for f in match.group(1).split(",")]
            missing = [f for f in files if not (path / f).exists()]
            passed = len(missing) == 0
            return passed, f"Missing: {missing}" if missing else "All required files present"
        return True, "No specific files listed"
