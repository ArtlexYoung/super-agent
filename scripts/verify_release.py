"""Run the local release checks for v0.2.104."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

VERSION = "0.2.104"
MAX_SOURCE_FILES = 37
# 源码非空行预算随功能按需增长：CLI 连续聊天、会话保存恢复、模型诊断、编码验证、Skill 管理。
# 通用能力需求（capability.py）、内容寻址（content.py）、自包含资源包（package.py）、
# Harness 兼容报告（harness.py）、声明式投影（projection.py）、能力解析（resolve.py）、
# 安装事务（transaction.py）、受限 MCP 出口（exit.py）与 Harness 预设（preset.py）新增到
# skill 层，随 P2-02/P2-04/P2-05 按 development-plan P0-00 调整。
# 按 development-plan P0-00 调整，保留增长预算但不取消版本、依赖与资源一致性检查。
MAX_SOURCE_LINES = 14_400
SOURCE_ROOTS = {"super_agent", "cli.py"}
DOMAIN_FILES = {
    "super_agent/adapter": {"cli.py", "database.py", "process.py", "storage.py", "tools.py"},
    "super_agent/core": {"__init__.py", "config.py", "context.py", "event.py", "model.py", "provider.py", "records.py", "resources.py", "run.py", "user.py"},
    "super_agent/skill": {
        "agent_builder.py",
        "builtin",
        "capability.py",
        "content.py",
        "document.py",
        "evolution.py",
        "exit.py",
        "harness.py",
        "library.py",
        "memory.py",
        "organization.py",
        "organization_runtime.py",
        "organization_tasks.py",
        "organization_tools.py",
        "organization_workers.py",
        "package.py",
        "preset.py",
        "projection.py",
        "resolve.py",
        "transaction.py",
    },
}
OLD_PATH_PARTS = {"discovery", "handlers", "learning", "tasks", "cli_support", "storage_backends"}
EVALUATION_FILES = (
    "tests/eval/README.md",
    "tests/eval/reports/token-usage-glm4-9b-20260806/README.md",
    "tests/eval/reports/token-usage-glm4-9b-20260806/humaneval_plus.md",
    "tests/eval/reports/token-usage-glm4-9b-20260806/livecodebench_codegen.md",
    "tests/eval/reports/token-usage-glm4-9b-20260806/summary.json",
    "tests/eval/reports/token-usage-glm4-9b-20260806/task-token-usage.csv",
    "tests/eval/reports/token-usage-glm4-9b-20260806/versions.json",
    "tests/eval/runner/common.py",
    "tests/eval/runner/raw_generate.py",
    "tests/eval/runner/report_token_usage.py",
    "tests/eval/runner/score_humaneval.py",
    "tests/eval/runner/score_livecodebench.py",
    "tests/eval/runtime/proxy/openllm_adapter.py",
    "tests/eval/runtime/proxy/siliconflow.yaml",
    "tests/eval/runtime/proxy/usage_logging.py",
)
WHEEL_ROOTS = ["src/super_agent"]
SDIST_ROOTS = ["README.md", "README_cn.md", "README_en.md", "pyproject.toml", "docs", "scripts", "src", "tests", "examples"]
REQUIRED_BUILTIN_SKILLS = {
    "common": set(),
    "session": {"conversation"},
    "audit": set(),
    "policy": set(),
    "research": {"citations"},
    "knowledge": {"project-context"},
    "automation": {"event-driven"},
    "code": {"deep-optimization"},
    "evolution": {"freshness", "self-update"},
    "memory": set(),
    "task": set(),
    "team": {"multi-agent"},
    "review": {"multi-agent"},
    "model-routing": set(),
    "context-window": set(),
    "context-compression": set(),
}


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args(arguments)
    root = Path(__file__).resolve().parents[1]
    errors = verify_release(root, args.version)
    if errors:
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        return 1
    print(f"Release checks passed: {args.version}")
    if args.full:
        errors = run_full_release_gate(root)
        if errors:
            for error in errors:
                print(f"FAIL {error}", file=sys.stderr)
            return 1
        print("Full release gate passed")
    return 0


def verify_release(root: Path, expected_version: str) -> list[str]:
    errors: list[str] = []
    if expected_version != VERSION:
        errors.append(f"this release gate is for {VERSION}")
    project = _read_toml(root / "pyproject.toml", errors)
    project_data = project.get("project", {})
    if not isinstance(project_data, dict):
        errors.append("pyproject.toml project table is missing")
        project_data = {}
    if project_data.get("version") != expected_version:
        errors.append("pyproject.toml version does not match")
    if project_data.get("requires-python") != ">=3.11":
        errors.append("Python 3.11 remains the supported minimum")
    if project_data.get("dependencies") != []:
        errors.append("default runtime dependencies must remain empty")
    if project_data.get("scripts", {}).get("super-agent") != "super_agent.adapter.cli:main":
        errors.append("installed CLI must point to super_agent.adapter.cli:main")
    if _read_version(root / "src/super_agent/core/__init__.py", errors) != expected_version:
        errors.append("core version does not match")
    for removed in ("web", "docs/ag-ui.md", "docs/web.md", "src/adapter/http", "src/adapter/static"):
        if (root / removed).exists():
            errors.append(f"removed interface remains: {removed}")
    errors.extend(_check_layout(root / "src"))
    errors.extend(_check_build_config(project, root))
    files = [path for path in (root / "src").rglob("*.py") if "__pycache__" not in path.parts]
    lines = sum(_non_empty_lines(path) for path in files)
    if len(files) > MAX_SOURCE_FILES:
        errors.append(f"source file count is {len(files)}, limit is {MAX_SOURCE_FILES}")
    if lines > MAX_SOURCE_LINES:
        errors.append(f"source line count is {lines}, limit is {MAX_SOURCE_LINES}")
    errors.extend(_check_dependency_direction(root / "src" / "super_agent"))
    errors.extend(_check_old_imports(files))
    errors.extend(_check_builtin_plugins(root / "src" / "super_agent" / "skill" / "builtin"))
    errors.extend(_check_benchmark(root / "examples" / "offline-gate-benchmark.json"))
    print(
        f"Python source: {len(files)} files, {lines} non-empty lines "
        f"(core={_domain_lines(root / 'src' / 'super_agent' / 'core')}, "
        f"skill={_domain_lines(root / 'src' / 'super_agent' / 'skill')}, "
        f"adapter={_domain_lines(root / 'src' / 'super_agent' / 'adapter')})"
    )
    readme = (root / "README.md").read_text(encoding="utf-8") if (root / "README.md").is_file() else ""
    for marker in ("README_cn.md", "README_en.md", "## 致谢与借鉴"):
        if marker not in readme:
            errors.append(f"README.md must contain {marker}")
    return errors


def run_full_release_gate(root: Path) -> list[str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(root / "src")
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="super-agent-release-") as temporary:
        output = Path(temporary) / "benchmark"
        for name, command in build_full_gate_commands(root, output):
            try:
                result = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True, check=False)
            except OSError as error:
                errors.append(f"{name} could not start: {error}")
                continue
            if result.returncode:
                detail = (result.stderr or result.stdout).strip()[-8_000:]
                errors.append(f"{name} exited {result.returncode}: {detail}")
            else:
                print(f"PASS {name}")
    return errors


def build_full_gate_commands(root: Path, output: Path) -> list[tuple[str, tuple[str, ...]]]:
    python = sys.executable
    commands = [
        ("Python tests", (python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py")),
        ("Python compile", (python, "-m", "compileall", "-q", "src")),
        ("diff check", ("git", "diff", "--check", "HEAD")),
        ("Python package build", ("uv", "build", str(root), "--out-dir", str(output.parent / "packages"), "--no-python-downloads", "--no-progress")),
        ("offline benchmark", (python, str(root / "scripts" / "run_benchmark.py"), "--manifest", str(root / "examples" / "offline-gate-benchmark.json"), "--output", str(output))),
    ]
    return commands


def _check_layout(source: Path) -> list[str]:
    errors: list[str] = []
    actual = {path.name for path in source.iterdir() if path.name != "__pycache__"}
    if actual != SOURCE_ROOTS:
        errors.append(f"src layout changed: {sorted(actual)}")
    for directory, expected in DOMAIN_FILES.items():
        path = source / directory
        if not path.is_dir():
            errors.append(f"missing src/{directory}")
            continue
        actual_children = {item.name for item in path.iterdir() if item.name != "__pycache__"}
        if actual_children != expected:
            errors.append(f"src/{directory} layout changed: {sorted(actual_children)}")
    for path in source.rglob("*"):
        if path.is_dir() and path.name in OLD_PATH_PARTS:
            errors.append(f"old architecture directory remains: {path.relative_to(source)}")
    return errors


def _check_build_config(project: dict[str, object], root: Path) -> list[str]:
    errors: list[str] = []
    targets = project.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {})
    wheel = targets.get("wheel", {})
    sdist = targets.get("sdist", {})
    if wheel.get("only-include") != WHEEL_ROOTS or wheel.get("sources") != ["src"]:
        errors.append("wheel must contain only the current source roots")
    if sdist.get("only-include") != SDIST_ROOTS:
        errors.append("sdist source roots changed")
    expected_force = {path: path for path in EVALUATION_FILES}
    if sdist.get("force-include") != expected_force:
        errors.append("sdist evaluation allowlist changed")
    optional = project.get("project", {}).get("optional-dependencies", {})
    if not isinstance(optional, dict) or not {"mysql", "postgresql"} <= set(optional):
        errors.append("MySQL and PostgreSQL optional extras are required")
    for relative in EVALUATION_FILES:
        if not (root / relative).is_file():
            errors.append(f"missing evaluation asset: {relative}")
    return errors


def _check_dependency_direction(package: Path) -> list[str]:
    """核心不得反向导入装配层；检测 core -> skill/adapter 的顶层依赖。"""
    errors: list[str] = []
    reverse = re.compile(r"(?m)^(?:from|import)\s+super_agent\.(?:(skill|adapter))\.?")
    for path in (package / "core").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = reverse.match(line)
            if match:
                errors.append(
                    f"core reverse-imports {match.group(1)}: "
                    f"{path.relative_to(package)}:{index}"
                )
    return errors


def _domain_lines(root: Path) -> int:
    """统计某源码目录的非空行，用于可解释的规模报告。"""
    if not root.is_dir():
        return 0
    return sum(
        _non_empty_lines(path)
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _check_old_imports(files: list[Path]) -> list[str]:
    errors: list[str] = []
    forbidden = ("PluginCatalog", "plugin_catalog", "plugin_paths", "writable_plugin_path", "plugin_cache_path", "plugin_snapshot")
    legacy_import = re.compile(r"(?<!super_agent\.)(^\s*(?:from|import)\s+(core|skill|adapter)(?:\.|\b))", re.MULTILINE)
    for path in files:
        text = path.read_text(encoding="utf-8")
        for name in forbidden:
            if name in text:
                errors.append(f"old import or compatibility reference remains: {path}: {name}")
        if legacy_import.search(text):
            errors.append(f"unprefixed internal import remains: {path}")
    return errors


def _check_builtin_plugins(root: Path) -> list[str]:
    errors: list[str] = []
    try:
        from super_agent.skill.library import AgentLibrary

        snapshot = AgentLibrary((root,)).snapshot()
    except (OSError, TypeError, ValueError) as error:
        return [f"builtin library validation failed: {error}"]
    expected_plugins = {f"super-agent/{name}" for name in REQUIRED_BUILTIN_SKILLS}
    if set(snapshot.plugins) != expected_plugins:
        errors.append(f"builtin plugin set changed: {sorted(snapshot.plugins)}")
    for label, resources in (
        ("Skill", snapshot.skills),
        ("MCP", snapshot.mcps),
        ("plugin", snapshot.plugins),
    ):
        for reference, resource in resources.items():
            if resource.version != VERSION:
                errors.append(f"builtin {label} version is invalid: {reference}")
            if len(resource.sources) != 1:
                errors.append(f"builtin {label} has duplicate sources: {reference}")
    directories = {path.name for path in root.iterdir() if path.is_dir()}
    if directories != {"plugins", "skills"}:
        errors.append(f"builtin library layout changed: {sorted(directories)}")
    for plugin_name, expected_members in REQUIRED_BUILTIN_SKILLS.items():
        plugin = root / "plugins" / "super-agent" / plugin_name
        manifest = _read_toml(plugin / "plugin.toml", errors)
        if manifest.get("schema") != 1 or manifest.get("version") != VERSION:
            errors.append(f"builtin plugin manifest is invalid: {plugin_name}")
        required_fields = {
            "description",
            "entry_skill",
            "skills",
            "included_plugins",
            "required_mcp_servers",
            "optional_mcp_servers",
        }
        if not required_fields <= set(manifest):
            errors.append(f"builtin plugin references are incomplete: {plugin_name}")
        skill_root = root / "skills" / "super-agent" / plugin_name
        members = {
            path.parent.name for path in skill_root.glob("*/SKILL.md")
        }
        if members != expected_members:
            errors.append(f"builtin plugin members changed: {plugin_name}: {sorted(members)}")
    for path in sorted(root.rglob("SKILL.md")):
        try:
            text = path.read_text(encoding="utf-8")
            if not text.startswith("---\n"):
                errors.append(f"builtin Skill is not standard: {path}")
            for forbidden in (
                "super-agent-created-by",
                "super-agent-agent-can-update",
            ):
                if forbidden in text:
                    errors.append(f"builtin Skill grants its own permission: {path}")
        except OSError as error:
            errors.append(f"cannot read builtin Skill {path}: {error}")
    return errors


def _check_benchmark(path: Path) -> list[str]:
    errors: list[str] = []
    value = _read_json(path, errors)
    agents = value.get("agents")
    tasks = value.get("tasks")
    if not isinstance(agents, list) or len(agents) != 1 or not isinstance(agents[0], dict):
        return [*errors, "offline benchmark must declare one Agent"]
    agent = agents[0]
    if agent.get("command") != ["{python}", "{project_root}/src/cli.py", "--output", "json", "{prompt}"]:
        errors.append("offline benchmark must execute the real CLI")
    if agent.get("environment") != {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "{project_root}/src", "SUPER_AGENT_PROVIDER": "mock"}:
        errors.append("offline benchmark must use explicit Mock Provider")
    if agent.get("result_json_field") != "text":
        errors.append("offline benchmark must inspect structured text")
    if not isinstance(tasks, list) or not tasks:
        errors.append("offline benchmark must contain a task")
    else:
        checks = tasks[0].get("checks", {}) if isinstance(tasks[0], dict) else {}
        if checks.get("workspace_unchanged") is not True:
            errors.append("offline benchmark must prove stateless workspace behavior")
        if "Mock response" not in checks.get("output_contains", []):
            errors.append("offline benchmark must verify Mock response")
    return errors


def _read_toml(path: Path, errors: list[str]) -> dict[str, object]:
    try:
        with path.open("rb") as stream:
            value = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        errors.append(f"cannot read {path}: {error}")
        return {}
    return value


def _read_json(path: Path, errors: list[str]) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"cannot read {path}: {error}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path} must contain an object")
        return {}
    return value


def _read_version(path: Path, errors: list[str]) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        errors.append(f"cannot read {path}: {error}")
        return None
    marker = '__version__ = "'
    if marker not in text:
        errors.append("core __version__ assignment is missing")
        return None
    return text.split(marker, 1)[1].split('"', 1)[0]


def _non_empty_lines(path: Path) -> int:
    return sum(bool(line.strip()) for line in path.read_text(encoding="utf-8").splitlines())


if __name__ == "__main__":
    raise SystemExit(main())
