# Module SPEC: evaluator/

## Purpose

Automated evaluation engine that verifies code artifacts against predefined success criteria.
Supports test execution (pytest), code coverage checks, lint validation (flake8/ruff), file
existence verification, critical marker detection, bug-fix verification (pattern absent/present,
file changed), and autofix. Integrates with the session store to emit evaluation lifecycle events.

The module is organized into focused sub-modules:

| Module | Purpose |
|--------|---------|
| `evaluator/engine.py` | `EvaluatorEngine` class -- public API, criteria dispatch, scoring |
| `evaluator/runner.py` | Stateless test/lint/coverage/import execution helpers |
| `evaluator/models.py` | `EvaluationContext`, `CheckResult`, `CheckSeverity` models |
| `evaluator/artifact.py` | Artifact path resolution and scope filtering |
| `evaluator/compat.py` | Legacy string criteria compatibility adapter (Chinese keyword mapping) |
| `evaluator/checkers/` | Pluggable criterion checkers |
| `evaluator/checkers/base.py` | `CriterionChecker` protocol |
| `evaluator/checkers/file_exists.py` | `FileExistsChecker` -- FILE_EXISTS, FILE_PATTERN, TEST_FILE_EXISTS |
| `evaluator/checkers/bugfix_patterns.py` | `BugfixPatternChecker` -- FILE_CHANGED, PATTERN_ABSENT, PATTERN_PRESENT |
| `evaluator/lint/` | Lint output parsing and delta classification |
| `evaluator/lint/parser.py` | `LintIssue` dataclass, `parse_flake8_output()`, `get_changed_lines()` |

Source: `evaluator/engine.py` (main entry point), with sub-modules extracted for maintainability.

---

## Public Interfaces

### Class `EvaluatorEngine` (engine.py)

```python
class EvaluatorEngine:
    def __init__(self, session_store: SessionStore) -> None
```

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `session_store` | `SessionStore` | Used to emit `EVAL_START` and `EVAL_RESULT` events |

**Public Methods:**

| Method | Signature | Returns | Description |
|--------|-----------|---------|-------------|
| `evaluate_stage` | `(session_id: str, stage_name: str, criteria: list[str], artifact_path: str) -> EvaluationResult` | `EvaluationResult` | Evaluates a stage against its success criteria, emits events, returns result |

**Private Methods:**

| Method | Signature | Returns | Description |
|--------|-----------|---------|-------------|
| `_check_criterion` | `(criterion: str, artifact_path: str) -> tuple[bool, str, bool]` | `(passed, message, was_auto_checked)` | Dispatches to the appropriate checker based on keyword matching |
| `_run_tests` | `(path: Path) -> tuple[bool, str]` | `(passed, message)` | Runs `python -m pytest {path} -v --tb=short` with 120s timeout |
| `_check_coverage` | `(path: Path, target: int) -> tuple[bool, str]` | `(passed, message)` | Runs pytest with `--cov=.` and parses TOTAL line for percentage |
| `_run_lint` | `(path: Path) -> tuple[bool, str]` | `(passed, message)` | Runs flake8 (falls back to ruff) with `--max-line-length=100` |
| `_check_files_exist` | `(criterion: str, path: Path) -> tuple[bool, str]` | `(passed, message)` | Parses filenames from criterion text, checks existence |
| `_check_no_critical_issues` | `(path: Path) -> tuple[bool, str]` | `(passed, message)` | Scans files for TODO/FIXME/XXX/HACK markers |
| `_extract_percentage` | `(text: str) -> int \| None` | `int \| None` | Extracts first `NN%` pattern from text |

---

### Models (evaluator/models.py)

#### `CheckSeverity(str, Enum)`

| Value | Description |
|-------|-------------|
| `NORMAL` | Standard automated check result |
| `WARNING` | Could not auto-verify; manual review recommended |
| `ERROR` | Check execution itself failed |

#### `EvaluationContext(BaseModel)`

Context passed to every criterion checker.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `work_dir` | `Path` | required | Working directory |
| `node_id` | `str \| None` | `None` | DAG node ID |
| `artifacts` | `list[str] \| None` | `None` | Artifact paths |
| `session_store` | `Any` | `None` | SessionStore reference |

#### `CheckResult(BaseModel)`

Result from a single criterion check.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `passed` | `bool` | required | Whether the check passed |
| `message` | `str` | required | Check message |
| `severity` | `CheckSeverity` | `NORMAL` | Result severity |
| `metadata` | `dict[str, Any]` | `{}` | Extra metadata |

---

### Checkers (evaluator/checkers/)

#### `CriterionChecker` Protocol (base.py)

```python
class CriterionChecker(Protocol):
    def check(self, criterion: SuccessCriterion, context: EvaluationContext) -> CheckResult: ...
```

#### `FileExistsChecker` (file_exists.py)

Handles: `FILE_EXISTS`, `FILE_PATTERN`, `TEST_FILE_EXISTS`.

#### `BugfixPatternChecker` (bugfix_patterns.py)

Handles: `FILE_CHANGED`, `PATTERN_ABSENT`, `PATTERN_PRESENT`.

---

### Runner (evaluator/runner.py)

Stateless execution helpers extracted from `EvaluatorEngine`. Key functions:

| Function | Description |
|----------|-------------|
| `safe_eval_id(eval_id)` | Sanitize eval_id for use as filename |
| `isolated_env(eval_id, work_dir)` | Build env with unique COVERAGE_FILE for parallel safety |
| `run_tests(path)` | Run pytest with 120s timeout |
| `check_coverage(path, target)` | Run pytest with coverage, parse TOTAL line |
| `run_lint(path)` | Run flake8 (fallback to ruff), max-line-length=100 |
| `check_files_exist(criterion, path)` | Parse filenames from criterion, verify existence |
| `check_no_critical(path)` | Scan for TODO/FIXME/XXX/HACK markers |
| `extract_percentage(text)` | Extract first NN% from text |
| `auto_fix_unused(path)` | Autofix unused imports/variables |
| `auto_format_apply(path)` | Apply code formatting |
| `detect_shadowing_test_inits(path)` | Detect shadowing __init__.py in test dirs |
| `import_smoke_test(path)` | Verify imports work |
| `find_test_files(path)` | Discover test files |

---

### Lint (evaluator/lint/)

#### `LintIssue` (dataclass, parser.py)

| Field | Type | Description |
|-------|------|-------------|
| `path` | `str` | Relative path to work_dir |
| `line` | `int` | Line number |
| `col` | `int` | Column number |
| `code` | `str` | Lint code (e.g., "E501") |
| `message` | `str` | Issue message |

#### Functions (parser.py)

| Function | Description |
|----------|-------------|
| `parse_flake8_output(output)` | Parse flake8 stdout into `list[LintIssue]` |
| `get_changed_lines(...)` | Delta classification for changed files |

---

### Compat (evaluator/compat.py)

Legacy string criteria adapter that bridges plain-text criteria (English and Chinese keywords) to structured `SuccessCriterion` instances.

| Function | Description |
|----------|-------------|
| `normalize_criteria(criteria)` | Parse mixed `list[str \| SuccessCriterion]` into `list[SuccessCriterion]` |
| `parse_string_criterion(criterion)` | Convert plain-text criterion string to `SuccessCriterion` |

Chinese keyword mapping: "测试" -> "test", "覆盖率" -> "coverage", "文件" -> "file", etc.

---

### Artifact (evaluator/artifact.py)

Artifact path resolution and scope filtering. Stateless functions.

| Function | Description |
|----------|-------------|
| `resolve_artifact_path(artifact, eval_root)` | Resolve artifact path with loose fallback |
| `scope_artifacts_to_criteria(...)` | Filter artifacts relevant to criteria |

---

## Data Flow

```
evaluate_stage(session_id, stage_name, criteria, artifact_path)
    |
    v
emit_event(session_id, EVAL_START, {stage, criteria, artifact})
    |
    v
compat.normalize_criteria(criteria) --> list[SuccessCriterion]
    |
    v
for each criterion in criteria:
    |
    v
    _check_criterion(criterion, artifact_path)
        |
        +-- "test" + "pass" in criterion ---> _run_tests(path)
        |       Runs: python -m pytest {path} -v --tb=short
        |
        +-- "coverage" in criterion --------> _check_coverage(path, target)
        |       Runs: python -m pytest {path} --cov=. --cov-report=term-missing
        |       Parses TOTAL line for coverage percentage
        |       Default target: 80%
        |
        +-- "lint"/"clean" in criterion ----> _run_lint(path)
        |       Runs: python -m flake8 {path} --max-line-length=100
        |       Fallback: ruff check {path}
        |
        +-- "file" + "exist" in criterion --> _check_files_exist(criterion, path)
        |       Parses filenames after colon/space from criterion text
        |
        +-- "no_critical"/"no bug" ---------> _check_no_critical_issues(path)
        |       Scans for TODO, FIXME, XXX, HACK markers
        |
        +-- (unrecognized) -----------------> (False, "not automatically checkable", False)
    |
    v
    Accumulate: results dict, score, feedback, uncheckable list
    |
    v
overall_passed = all(results) AND no uncheckable criteria
score = (passed_count / total_count) * 10.0, rounded to 1 decimal
    |
    v
EvaluationResult(passed, score, criteria_results, feedback, suggestions)
    |
    v
emit_event(session_id, EVAL_RESULT, result.model_dump())
    |
    v
return EvaluationResult
```

## Error Codes

No custom error codes. All errors are captured and returned as `(False, message)` tuples:

| Condition | Error Message | Method |
|-----------|---------------|--------|
| pytest not installed | `"pytest not installed"` | `_run_tests` |
| Test execution failure | `"Test execution error: {e}"` | `_run_tests` |
| Tests failed | `"Tests failed:\n{last 500 chars of stdout}"` | `_run_tests` |
| Coverage parse failure | `"Could not parse coverage report"` | `_check_coverage` |
| Coverage check error | `"Coverage check error: {e}"` | `_check_coverage` |
| No linter available | `"No linter available (install flake8 or ruff)"` | `_run_lint` |
| Lint error | `"Lint error: {e}"` | `_run_lint` |
| Unrecognized criterion | `"Criterion '{criterion}' is not automatically checkable..."` | `_check_criterion` |

## Dependencies

| Dependency | Type | Usage |
|------------|------|-------|
| `core.models` | Internal | `EvaluationResult`, `EvalStatus`, `EventType`, `SuccessCriterion`, `CriterionType` |
| `evaluator.models` | Internal | `EvaluationContext`, `CheckResult`, `CheckSeverity` |
| `evaluator.runner` | Internal | Stateless test/lint/coverage execution functions |
| `evaluator.compat` | Internal | Legacy criteria normalization |
| `evaluator.artifact` | Internal | Artifact path resolution |
| `evaluator.lint.parser` | Internal | `LintIssue`, `parse_flake8_output` |
| `evaluator.checkers` | Internal | `CriterionChecker` protocol, `FileExistsChecker`, `BugfixPatternChecker` |
| `session.store` | Internal | `SessionStore` for event emission |
| `subprocess` | Stdlib | Running pytest, flake8, ruff as child processes |
| `pathlib.Path` | Stdlib | File path handling |
| `re` | Stdlib | Parsing filenames from criteria, extracting percentages |

## Configuration

| Parameter | Default | Scope | Description |
|-----------|---------|-------|-------------|
| Test timeout | `120` seconds | `runner.run_tests` | `subprocess.run` timeout for pytest |
| Coverage timeout | `120` seconds | `runner.check_coverage` | `subprocess.run` timeout for coverage run |
| Lint timeout | `60` seconds | `runner.run_lint` | `subprocess.run` timeout for flake8/ruff |
| Max line length | `100` | `runner.run_lint` | `--max-line-length=100` flag for flake8 |
| Default coverage target | `80`% | `engine._check_criterion` | Used when no percentage is found in the criterion text |
| Max score | `10.0` | `evaluate_stage` | Divided equally among criteria |
| Test stdout truncation | Last 500 chars | `runner.run_tests` | Limit on failure output |
| Lint stdout truncation | First 500 chars | `runner.run_lint` | Limit on failure output |

## Extension Points

1. **New criterion types**: Add a new `CriterionType` enum value, implement a `CriterionChecker` in `evaluator/checkers/`, and add keyword dispatch in `_check_criterion()`.
2. **Custom test runners**: Replace the `subprocess.run(["python", "-m", pytest, ...])` call in `runner.run_tests()` to support other test frameworks.
3. **Scoring strategy**: The current scoring divides 10.0 equally among all criteria. A weighted scoring system could be introduced by accepting a `dict[str, float]` of criterion-to-weight mappings.
4. **External linters**: The fallback chain (flake8 -> ruff) in `runner.run_lint()` can be extended with additional tools.
5. **Critical markers**: The list `["TODO", "FIXME", "XXX", "HACK"]` in `runner.check_no_critical()` is hardcoded and could be made configurable.
6. **New checkers**: Implement `CriterionChecker` protocol and register in the engine dispatch table.

## Invariants

1. **Uncheckable criteria are never treated as passed**: If `_check_criterion` returns `was_auto_checked=False`, the overall `passed` is always `False` regardless of other results.
2. **Every evaluation emits exactly two events**: One `EVAL_START` at the beginning and one `EVAL_RESULT` at the end.
3. **Score is bounded**: `score` is computed as `(passed_count / total_count) * 10.0`, rounded to 1 decimal. It reflects only auto-checked criteria that passed, not overall pass/fail.
4. **Subprocess isolation**: All external tool invocations use `subprocess.run()` with timeouts and `capture_output=True`. Failures return `(False, message)` and never propagate exceptions.
5. **Output truncation**: Test failure output is truncated to the last 500 characters; lint output is truncated to the first 500 characters. This prevents unbounded log growth.
6. **Criterion matching is case-insensitive**: `criterion.lower()` is used for all keyword matching in `_check_criterion()`.
7. **No mutation of inputs**: `evaluate_stage()` does not modify `criteria`, `artifact_path`, or any `SessionStore` state beyond emitting events.
8. **Structured criteria backward compatible**: `compat.normalize_criteria()` accepts mixed `list[str | SuccessCriterion]` and converts all to `SuccessCriterion`.
9. **Coverage file isolation**: `runner.isolated_env()` sets a unique `COVERAGE_FILE` per evaluation to prevent parallel node contention.
