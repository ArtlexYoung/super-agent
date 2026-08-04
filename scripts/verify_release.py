"""Run the dependency-free static checks required before a local release."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tomllib
from pathlib import Path


MAX_SOURCE_FILES = 83
MAX_SOURCE_LINES = 17_000
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
VERSION_PATTERN = re.compile(r"0\.0\.\d+$")


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="release version to verify")
    args = parser.parse_args(arguments)
    root = Path(__file__).resolve().parents[1]
    errors = verify_release(root, args.version)
    if errors:
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        return 1
    print(f"Release checks passed: {args.version}")
    return 0


def verify_release(root: Path, expected_version: str) -> list[str]:
    errors: list[str] = []
    if VERSION_PATTERN.fullmatch(expected_version) is None:
        errors.append("version must use the 0.0.x format")

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
    source_lines = sum(
        len(path.read_text(encoding="utf-8").splitlines()) for path in source_files
    )
    if len(source_files) >= MAX_SOURCE_FILES:
        errors.append(f"source file count must stay below {MAX_SOURCE_FILES}")
    if source_lines >= MAX_SOURCE_LINES:
        errors.append(f"source line count must stay below {MAX_SOURCE_LINES}")
    readme = root / "README.md"
    if not readme.is_file() or "README_cn.md" not in readme.read_text(encoding="utf-8"):
        errors.append("README.md must link to README_cn.md")
    return errors


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
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
    errors.append("src/core/__init__.py must assign a string __version__")
    return None


if __name__ == "__main__":
    raise SystemExit(main())
