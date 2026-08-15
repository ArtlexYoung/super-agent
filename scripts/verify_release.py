"""Run the dependency-free static checks required before a local release."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


MAX_TOTAL_SOURCE_FILES = 48
SOURCE_LINE_BUDGETS = {
    "0.1.81": 18_602, "0.1.82": 17_500, "0.1.83": 16_200,
    "0.1.84": 15_300, "0.1.85": 14_100, "0.1.86": 13_000,
    "0.1.87": 11_900, "0.1.88": 10_900, "0.1.89": 10_200,
    "0.1.90": 9_750, "0.1.91": 9_749, "0.1.92": 9_748,
    "0.1.93": 9_747, "0.1.94": 9_746, "0.1.95": 9_745,
    "0.1.96": 9_744,
}
FINAL_SOURCE_LINE_TARGET = 9_744
MAX_TOTAL_SOURCE_LINES = SOURCE_LINE_BUDGETS["0.1.96"]
EXPECTED_SOURCE_ROOT = {"adapter", "cli.py", "core", "skill", "super_agent.py"}
EXPECTED_DOMAIN_CHILDREN = {
    "adapter": {
        "agent.py", "cli.py", "cli_support", "code.py", "http", "processes.py",
        "repository.py", "static", "storage_backends", "user.py",
    },
    "core": {
        "checks.py", "config.py", "loop.py", "model_calls.py", "models.py",
        "provider.py", "records", "runtime.py", "team.py", "tools.py",
    },
    "skill": {"builtin", "discovery", "handlers", "learning", "tasks"},
}
EXPECTED_WHEEL_ROOTS = [
    "src/adapter",
    "src/core",
    "src/skill",
    "src/cli.py",
    "src/super_agent.py",
]
EXPECTED_SDIST_ROOTS = [
    "README.md",
    "README_cn.md",
    "pyproject.toml",
    "docs",
    "scripts",
    "src",
    "tests",
    "examples",
]
VERSION_PATTERN = re.compile(r"0\.\d+\.\d+$")
AGENT_OWNER_MODULES = {"adapter/agent.py", "adapter/user.py"}
EXPECTED_AGENT_ACTIONS = {
    "add_model",
    "add_skill_path",
    "add_subagent",
    "add_tool",
    "for_user",
    "run",
}
EXPECTED_AGENT_REGISTRATION_ACTIONS = {
    "AgentEvents": {"add_subscriber"},
    "AgentSkills": {"add_handler", "enable"},
}
PRIVATE_AGENT_CALLS = {
    "_action_rules",
    "_create_event_store",
    "_create_skills",
    "_create_task_runner",
    "_execute_action",
    "_read_task_trace",
    "_record_task_feedback",
    "_reload_models",
    "_replace_configuration",
    "_run_for_user",
    "_user_environment",
    "_uses_direct_provider",
    "_add_skill_handler",
    "_add_event_subscriber",
}
REMOVED_CODE_NAMES = {
    "AgentChoice",
    "AgentResources",
    "AgentTask",
    "AgentTaskQueue",
    "AgentTaskQueueSettings",
    "AuditSettings",
    "SubagentPool",
    "_add_event_subscriber",
    "_add_skill_handler",
    "classify_audit_event",
    "compact_runtime_event_data",
    "prune_expired_audit_events",
    "redact_event_data_for_display",
    "redact_events_for_display",
    "create_agent_task_queue",
    "create_runtime_tools",
    "ModelCalls",
    "ModelCallContext",
    "ModelLoop",
    "RuntimeTools",
    "RuntimeToolsContext",
    "RunToolsContext",
    "RuntimeMemoryStore",
    "SkillCollection",
    "SkillResult",
    "create_run_tools",
}
PRESERVED_SOURCE_SYMBOLS = {
    "adapter/cli.py": {"main"},
    "adapter/cli_support/cli_data.py": {
        "configure_conversations_parser", "configure_memory_parser",
        "configure_runs_parser", "configure_storage_parser",
    },
    "adapter/cli_support/cli_skills.py": {
        "configure_skill_changes_parser", "configure_skill_packages_parser",
        "configure_models_parser", "run_skills_command",
    },
    "adapter/http/agui.py": {"create_ag_ui_server"},
    "adapter/http/web.py": {"WebAPI"},
    "adapter/storage_backends/local_storage.py": {"JsonlStorage", "SqliteStorage"},
    "adapter/storage_backends/remote_storage.py": {"MySqlStorage", "PostgreSqlStorage"},
    "core/model_calls.py": {"list_model_usage_stats"},
    "core/models.py": {"SubagentRecordOptions"},
    "core/records/audit.py": {"compact_subagent_result"},
    "core/records/store.py": {"EventStore", "StorageBackend"},
    "core/runtime.py": {"Run", "Runtime"},
    "core/tools.py": {"RunTools"},
    "skill/handlers/memory.py": {"Memory"},
    "skill/handlers/package.py": {
        "SkillPackageManager", "apply_skill_directory_updates", "write_skill_lock_file",
    },
    "skill/learning/update.py": {
        "SkillUpdater",
    },
    "skill/learning/run_learning.py": {
        "explain_run_with_insight", "learn_from_run", "review_run_evidence",
    },
    "skill/tasks/task_groups.py": {"AgentGroups", "AgentGroupSettings"},
    "skill/tasks/task_queue.py": {"TaskQueue", "create_task_queue"},
    "skill/tasks/task_selection.py": {"AgentSelector", "TaskQueueSettings"},
}
PRESERVED_CLASS_METHODS = {
    ("skill/handlers/memory.py", "Memory"): {
        "forget_long_term", "list_long_term", "organize_long_term",
        "recall_long_term", "remember_long_term",
    },
    ("skill/learning/update.py", "SkillUpdater"): {
        "apply_skill_change", "propose_skill_change",
        "test_skill_change", "undo_skill_change",
    },
    ("skill/tasks/task_groups.py", "AgentGroups"): {
        "has_failures", "list_groups", "list_tools", "refresh",
    },
    ("skill/tasks/task_queue.py", "TaskQueue"): {
        "close", "finish", "list_groups", "list_tasks", "list_tools", "read_results",
    },
    ("skill/tasks/task_selection.py", "AgentSelector"): {
        "choose", "choose_group", "commit_group", "record_success",
        "record_unavailable", "retry_delay",
    },
}
PRESERVED_OPTIONAL_DEPENDENCIES = {"mysql", "postgresql"}
PRESERVED_SKILL_RESOURCES = {
    "task/code-multi-deep-optimization/SKILL.md",
    "task/code-multi-deep-optimization/skill.toml",
    "task/common-multi-producer-consumer/SKILL.md",
    "task/common-multi-producer-consumer/skill.toml",
}
FEATURE_CONTRACT_MODULES = (
    "tests.agent.test_public_api", "tests.agent.test_run",
    "tests.agent.test_model_discovery", "tests.agent.test_multi_agent_tasks",
    "tests.agent.test_record_compression", "tests.commands.test_cli",
    "tests.agui.test_protocol", "tests.agui.test_server",
    "tests.runtime.test_provider_calls", "tests.runtime.test_safety",
    "tests.runtime.test_stateless", "tests.skills.test_disclosure_core",
    "tests.skills.test_memory", "tests.skills.test_tasks", "tests.skills.test_packages",
    "tests.skills.test_skill_update", "tests.storage.test_storage",
    "tests.storage.test_remote_sql_storage", "tests.storage.test_audit",
)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="release version to verify")
    parser.add_argument("--full", action="store_true", help="run executable Python gates")
    parser.add_argument("--web", action="store_true", help="include Web checks in --full")
    args = parser.parse_args(arguments)
    if args.web and not args.full:
        parser.error("--web requires --full")
    root = Path(__file__).resolve().parents[1]
    errors = verify_release(root, args.version)
    if errors:
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        return 1
    print(f"Release checks passed: {args.version}")
    if args.full:
        full_errors = run_full_release_gate(root, include_web=args.web)
        if full_errors:
            for error in full_errors:
                print(f"FAIL {error}", file=sys.stderr)
            return 1
        print("Full release gate passed")
    return 0


def run_full_release_gate(root: Path, *, include_web: bool) -> list[str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(root / "src"), str(root / "tests")]
    )
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="super-agent-release-") as temporary:
        output = Path(temporary) / "benchmark"
        for name, command in build_full_gate_commands(root, output, include_web):
            try:
                completed = subprocess.run(
                    command,
                    cwd=root,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except OSError as error:
                errors.append(f"{name} could not start: {error}")
                continue
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()[-8_000:]
                errors.append(f"{name} exited {completed.returncode}: {detail}")
            else:
                print(f"PASS {name}")
    return errors


def build_full_gate_commands(
    root: Path,
    benchmark_output: Path,
    include_web: bool,
) -> list[tuple[str, tuple[str, ...]]]:
    python = sys.executable
    commands = [
        (
            "Feature contract",
            (python, "-m", "unittest", *FEATURE_CONTRACT_MODULES),
        ),
        (
            "Python tests",
            (python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"),
        ),
        ("Python compile", (python, "-m", "compileall", "-q", "src")),
        ("diff check", ("git", "diff", "--check")),
        (
            "Python package build",
            (
                "uv", "build", str(root), "--out-dir",
                str(benchmark_output.parent / "packages"),
                "--no-python-downloads", "--no-progress",
            ),
        ),
        (
            "offline benchmark",
            (
                python,
                str(root / "scripts" / "run_benchmark.py"),
                "--manifest",
                str(root / "examples" / "offline-gate-benchmark.json"),
                "--output",
                str(benchmark_output),
            ),
        ),
    ]
    if include_web:
        commands.extend(
            [
                ("Web typecheck", ("pnpm", "--dir", "web", "typecheck")),
                ("Web lint", ("pnpm", "--dir", "web", "lint")),
                ("Web build", ("pnpm", "--dir", "web", "build")),
            ]
        )
    return commands


def verify_release(root: Path, expected_version: str) -> list[str]:
    errors: list[str] = []
    if VERSION_PATTERN.fullmatch(expected_version) is None:
        errors.append("version must use the 0.x.y format")
    planned_budget = SOURCE_LINE_BUDGETS.get(expected_version)
    if planned_budget is None:
        errors.append("version has no locked source-line budget")
    elif planned_budget != MAX_TOTAL_SOURCE_LINES:
        errors.append("current source-line budget does not match the locked release plan")

    project = _read_toml(root / "pyproject.toml", errors)
    web_package = _read_json(root / "web/package.json", errors)
    project_data = project.get("project", {})
    if project_data.get("version") != expected_version:
        errors.append("pyproject.toml project.version does not match the requested version")
    if project_data.get("dependencies") != []:
        errors.append("default project dependencies must remain empty")
    if project_data.get("scripts", {}).get("super-agent") != "adapter.cli:main":
        errors.append("the installed CLI must point to adapter.cli:main")

    if _read_python_version(root / "src/core/__init__.py", errors) != expected_version:
        errors.append("src/core/__init__.py __version__ does not match the requested version")
    if web_package.get("version") != expected_version:
        errors.append("web/package.json version does not match the requested version")

    source_root = root / "src"
    actual_root = {
        path.name for path in source_root.iterdir() if path.name != "__pycache__"
    }
    if actual_root != EXPECTED_SOURCE_ROOT:
        errors.append(f"src layout changed: {sorted(actual_root)}")
    for name, expected in EXPECTED_DOMAIN_CHILDREN.items():
        actual = {
            path.name
            for path in (source_root / name).iterdir()
            if path.name not in {"__init__.py", "__pycache__"}
        }
        if actual != expected:
            errors.append(f"src/{name} layout changed: {sorted(actual)}")

    wheel = (
        project.get("tool", {})
        .get("hatch", {})
        .get("build", {})
        .get("targets", {})
        .get("wheel", {})
    )
    if wheel.get("only-include") != EXPECTED_WHEEL_ROOTS:
        errors.append("wheel source roots changed")
    if wheel.get("sources") != ["src"]:
        errors.append("wheel source mapping must remain ['src']")
    sdist = (
        project.get("tool", {})
        .get("hatch", {})
        .get("build", {})
        .get("targets", {})
        .get("sdist", {})
    )
    if sdist.get("only-include") != EXPECTED_SDIST_ROOTS:
        errors.append("sdist source roots changed")

    source_files = [
        path
        for path in source_root.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    source_lines = sum(_count_non_empty_lines(path) for path in source_files)
    if len(source_files) >= MAX_TOTAL_SOURCE_FILES:
        errors.append(
            f"source file count must stay below {MAX_TOTAL_SOURCE_FILES}"
        )
    if source_lines > MAX_TOTAL_SOURCE_LINES:
        errors.append(
            f"source line count must not exceed {MAX_TOTAL_SOURCE_LINES}"
        )
    errors.extend(_verify_owned_agent_calls(source_root, source_files))
    errors.extend(_verify_removed_code_names(source_files))
    errors.extend(_verify_agent_actions(source_root / "adapter" / "agent.py"))
    errors.extend(_verify_preserved_capabilities(source_root, project_data))
    errors.extend(
        _verify_offline_benchmark(root / "examples" / "offline-gate-benchmark.json")
    )
    readme = root / "README.md"
    if not readme.is_file() or "README_cn.md" not in readme.read_text(encoding="utf-8"):
        errors.append("README.md must link to README_cn.md")
    return errors


def _count_non_empty_lines(path: Path) -> int:
    return sum(
        1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    )


def _verify_owned_agent_calls(
    source_root: Path,
    source_files: list[Path],
) -> list[str]:
    errors = []
    for path in source_files:
        relative = path.relative_to(source_root).as_posix()
        if not relative.startswith("adapter/") or relative in AGENT_OWNER_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in PRIVATE_AGENT_CALLS
            ):
                errors.append(
                    f"{relative}:{node.lineno} calls private Agent method "
                    f"{node.func.attr}"
                )
    return errors


def _verify_removed_code_names(source_files: list[Path]) -> list[str]:
    errors = []
    for path in source_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = _defined_or_imported_names(node)
            for name in names & REMOVED_CODE_NAMES:
                errors.append(
                    f"removed code name returned: {name} at {path}:{node.lineno}"
                )
    return errors


def _verify_preserved_capabilities(
    source_root: Path,
    project_data: dict[str, object],
) -> list[str]:
    """Reject releases that remove a high-complexity feature to reduce source size."""
    errors = []
    for relative, required in PRESERVED_SOURCE_SYMBOLS.items():
        path = source_root / relative
        if not path.is_file():
            errors.append(f"preserved capability module is missing: {relative}")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        actual = {
            node.name
            for node in tree.body
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for symbol in sorted(required - actual):
            errors.append(f"preserved capability is missing: {relative}:{symbol}")
    for (relative, class_name), required in PRESERVED_CLASS_METHODS.items():
        path = source_root / relative
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        owner = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == class_name
            ),
            None,
        )
        actual = {
            node.name
            for node in (() if owner is None else owner.body)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for method in sorted(required - actual):
            errors.append(
                f"preserved capability is missing: {relative}:{class_name}.{method}"
            )
    optional = project_data.get("optional-dependencies", {})
    optional_names = set(optional) if isinstance(optional, dict) else set()
    for name in sorted(PRESERVED_OPTIONAL_DEPENDENCIES - optional_names):
        errors.append(f"preserved optional storage dependency is missing: {name}")
    builtin_root = source_root / "skill" / "builtin"
    actual_resources = {
        path.relative_to(builtin_root).as_posix()
        for path in builtin_root.rglob("*")
        if path.is_file()
    }
    for relative in sorted(PRESERVED_SKILL_RESOURCES - actual_resources):
        errors.append(f"preserved task Skill resource is missing: {relative}")
    return errors


def _defined_or_imported_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
        return {node.name}
    if isinstance(node, ast.ImportFrom | ast.Import):
        return {alias.asname or alias.name.rsplit(".", 1)[-1] for alias in node.names}
    return set()


def _verify_agent_actions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    classes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    expected = {
        "Agent": EXPECTED_AGENT_ACTIONS,
        **EXPECTED_AGENT_REGISTRATION_ACTIONS,
    }
    errors = []
    for name, expected_actions in expected.items():
        owner = classes.get(name)
        if owner is None:
            errors.append(f"Agent action owner is missing: {name}")
            continue
        actual = {
            node.name
            for node in owner.body
            if isinstance(node, ast.FunctionDef)
            and not node.name.startswith("_")
            and not _is_property(node)
        }
        if actual != expected_actions:
            errors.append(
                f"{name} actions changed: expected {sorted(expected_actions)}, "
                f"found {sorted(actual)}"
            )
    return errors


def _verify_offline_benchmark(path: Path) -> list[str]:
    errors: list[str] = []
    value = _read_json(path, errors)
    agents = value.get("agents")
    tasks = value.get("tasks")
    if not isinstance(agents, list) or len(agents) != 1 or not isinstance(agents[0], dict):
        return [*errors, "offline gate must declare one Agent"]
    agent = agents[0]
    expected_command = [
        "{python}", "{project_root}/src/cli.py", "--output", "json", "{prompt}",
    ]
    if agent.get("command") != expected_command:
        errors.append("offline gate must execute the real Super Agent CLI")
    expected_environment = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": "{project_root}/src",
        "SUPER_AGENT_PROVIDER": "mock",
    }
    if agent.get("environment") != expected_environment:
        errors.append("offline gate must use the explicit offline mock environment")
    if agent.get("result_json_field") != "text":
        errors.append("offline gate must verify the structured Agent result text")
    if not isinstance(tasks, list) or not tasks or not isinstance(tasks[0], dict):
        errors.append("offline gate must declare at least one task")
    else:
        checks = tasks[0].get("checks")
        if not isinstance(checks, dict) or checks.get("workspace_unchanged") is not True:
            errors.append("offline gate must prove that a stateless run leaves no files")
        if not isinstance(checks, dict) or "Mock response" not in checks.get("output_contains", []):
            errors.append("offline gate must verify the Provider result")
    return errors


def _is_property(function: ast.FunctionDef) -> bool:
    return any(
        isinstance(decorator, ast.Name) and decorator.id == "property"
        for decorator in function.decorator_list
    )


def _read_toml(path: Path, errors: list[str]) -> dict[str, object]:
    try:
        with path.open("rb") as source:
            value = tomllib.load(source)
    except (OSError, tomllib.TOMLDecodeError) as error:
        errors.append(f"cannot read {path.name}: {error}")
        return {}
    return value


def _read_json(path: Path, errors: list[str]) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"cannot read {path.name}: {error}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.name} must contain an object")
        return {}
    return value


def _read_python_version(path: Path, errors: list[str]) -> str | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        errors.append(f"cannot read {path.name}: {error}")
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        assigns_version = any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        )
        if assigns_version:
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
    errors.append("src/core/__init__.py must assign a string __version__")
    return None


if __name__ == "__main__":
    raise SystemExit(main())
