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
MAX_TOTAL_SOURCE_LINES = 18_700
EXPECTED_SOURCE_ROOT = {"adapter", "cli.py", "core", "skill", "super_agent.py"}
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
    "_create_task_loop",
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
}


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
            "Python tests",
            (python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"),
        ),
        ("Python compile", (python, "-m", "compileall", "-q", "src")),
        ("diff check", ("git", "diff", "--check")),
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

    project = _read_toml(root / "pyproject.toml", errors)
    web_package = _read_json(root / "web/package.json", errors)
    project_data = project.get("project", {})
    if project_data.get("version") != expected_version:
        errors.append("pyproject.toml project.version does not match the requested version")
    if project_data.get("dependencies") != []:
        errors.append("default project dependencies must remain empty")
    if project_data.get("scripts", {}).get("super-agent") != "adapter.cli_adapter.commands:main":
        errors.append("the installed CLI must point to adapter.cli_adapter.commands:main")

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
    if source_lines >= MAX_TOTAL_SOURCE_LINES:
        errors.append(
            f"source line count must stay below {MAX_TOTAL_SOURCE_LINES}"
        )
    errors.extend(_verify_owned_agent_calls(source_root, source_files))
    errors.extend(_verify_removed_code_names(source_files))
    errors.extend(_verify_agent_actions(source_root / "adapter" / "agent.py"))
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
