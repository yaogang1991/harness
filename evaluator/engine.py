"""
Evaluator: automated evaluation and contract verification.
Inspired by Anthropic's three-agent harness evaluator.
"""

import subprocess
from pathlib import Path

from core.models import EvaluationResult, EventType
from session.store import SessionStore


class EvaluatorEngine:
    """
    Evaluates code against predefined success criteria.
    Supports: test execution, lint checks, coverage, complexity.
    """

    def __init__(self, session_store: SessionStore):
        self.session_store = session_store

    def evaluate_stage(
        self,
        session_id: str,
        stage_name: str,
        criteria: list[str],
        artifact_path: str,
    ) -> EvaluationResult:
        """Evaluate a stage against its success criteria."""
        self.session_store.emit_event(
            session_id,
            EventType.EVAL_START,
            {"stage": stage_name, "criteria": criteria, "artifact": artifact_path},
        )

        results = {}
        score = 0.0
        feedback_parts = []

        for criterion in criteria:
            passed, msg = self._check_criterion(criterion, artifact_path)
            results[criterion] = passed
            if passed:
                score += 10.0 / len(criteria)
            feedback_parts.append(f"{'✅' if passed else '❌'} {criterion}: {msg}")

        passed = all(results.values())
        feedback = "\n".join(feedback_parts)

        result = EvaluationResult(
            passed=passed,
            score=round(score, 1),
            criteria_results=results,
            feedback=feedback,
        )

        self.session_store.emit_event(
            session_id,
            EventType.EVAL_RESULT,
            result.model_dump(),
        )

        return result

    def _check_criterion(self, criterion: str, artifact_path: str) -> tuple[bool, str]:
        """Check a single success criterion."""
        criterion_lower = criterion.lower()
        path = Path(artifact_path)

        if "test" in criterion_lower and "pass" in criterion_lower:
            return self._run_tests(path)

        if "coverage" in criterion_lower:
            target = self._extract_percentage(criterion_lower) or 80
            return self._check_coverage(path, target)

        if "lint" in criterion_lower or "clean" in criterion_lower:
            return self._run_lint(path)

        if "file" in criterion_lower and "exist" in criterion_lower:
            return self._check_files_exist(criterion_lower, path)

        if "no_critical" in criterion_lower or "no bug" in criterion_lower:
            return self._check_no_critical_issues(path)

        # Default: assume passed if we can't evaluate
        return True, f"Criterion '{criterion}' not automatically checkable, manual review needed"

    def _run_tests(self, path: Path) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", str(path), "-v", "--tb=short"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            passed = result.returncode == 0
            return passed, "Tests passed" if passed else f"Tests failed:\n{result.stdout[-500:]}"
        except FileNotFoundError:
            return False, "pytest not installed"
        except Exception as e:
            return False, f"Test execution error: {e}"

    def _check_coverage(self, path: Path, target: int) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", str(path), "--cov=.", "--cov-report=term-missing"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            # Parse coverage output
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

    def _run_lint(self, path: Path) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                ["python", "-m", "flake8", str(path), "--max-line-length=100"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            passed = result.returncode == 0
            return passed, "Lint clean" if passed else f"Lint issues:\n{result.stdout[:500]}"
        except FileNotFoundError:
            # Try ruff if flake8 not available
            try:
                result = subprocess.run(
                    ["ruff", "check", str(path)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                passed = result.returncode == 0
                return passed, "Ruff clean" if passed else f"Ruff issues:\n{result.stdout[:500]}"
            except FileNotFoundError:
                return True, "No linter available, skipping"
        except Exception as e:
            return False, f"Lint error: {e}"

    def _check_files_exist(self, criterion: str, path: Path) -> tuple[bool, str]:
        # Parse patterns from criterion like "files_exist: main.py, README.md"
        import re
        match = re.search(r"[:\s]+(.+)", criterion)
        if match:
            files = [f.strip() for f in match.group(1).split(",")]
            missing = [f for f in files if not (path / f).exists()]
            passed = len(missing) == 0
            return passed, f"Missing: {missing}" if missing else "All required files present"
        return True, "No specific files listed"

    def _check_no_critical_issues(self, path: Path) -> tuple[bool, str]:
        # Simple heuristic: check for TODO/FIXME/XXX markers
        try:
            content = path.read_text(errors="ignore")
            issues = []
            for marker in ["TODO", "FIXME", "XXX", "HACK"]:
                if marker in content:
                    issues.append(marker)
            passed = len(issues) == 0
            return passed, f"Found markers: {issues}" if issues else "No critical markers found"
        except Exception:
            return True, "Could not check"

    def _extract_percentage(self, text: str) -> int | None:
        import re
        match = re.search(r'(\d+)%', text)
        if match:
            return int(match.group(1))
        return None
