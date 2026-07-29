from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from core import __version__


class ReleaseShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = tomllib.loads(
            Path("pyproject.toml").read_text(encoding="utf-8")
        )

    def test_release_versions_have_one_value(self) -> None:
        web_package = json.loads(
            Path("web/package.json").read_text(encoding="utf-8")
        )

        self.assertEqual(__version__, self.project["project"]["version"])
        self.assertEqual(__version__, web_package["version"])

    def test_default_python_install_has_no_dependencies(self) -> None:
        self.assertEqual([], self.project["project"]["dependencies"])

    def test_wheel_contains_only_the_public_source_layout(self) -> None:
        wheel = self.project["tool"]["hatch"]["build"]["targets"]["wheel"]

        self.assertEqual(
            [
                "src/adapter",
                "src/core",
                "src/skill",
                "src/cli.py",
                "src/super_agent.py",
                "skill_scenes",
            ],
            wheel["only-include"],
        )
        self.assertEqual(["src"], wheel["sources"])

    def test_removed_source_layouts_do_not_return(self) -> None:
        removed_paths = [
            "src/builtin_skills",
            "src/capabilities",
            "src/commands",
            "src/frontend",
            "src/mcp",
            "src/memory",
            "src/provider_adapter",
            "src/runtime",
            "src/super_agent",
            "src/workflow",
        ]

        self.assertEqual([], [path for path in removed_paths if Path(path).exists()])

    def test_public_modules_import_in_a_fresh_process(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path("src").resolve())
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from super_agent import Agent; "
                    "from adapter.ag_ui_adapter import AGUIEventMapper; "
                    "from core.session import RuntimeSession; "
                    "from skill.manifest import SkillManifest",
                ],
                cwd=tmp,
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_python_source_files_stay_within_the_size_limit(self) -> None:
        oversized = {}
        for path in Path("src").rglob("*.py"):
            line_count = _count_non_import_lines(path)
            if line_count > 600:
                oversized[str(path)] = line_count

        self.assertEqual({}, oversized)


def _count_non_import_lines(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    import_lines: set[int] = set()
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if isinstance(node, ast.Import | ast.ImportFrom):
            import_lines.update(range(node.lineno, node.end_lineno + 1))
    return len(source.splitlines()) - len(import_lines)


if __name__ == "__main__":
    unittest.main()
