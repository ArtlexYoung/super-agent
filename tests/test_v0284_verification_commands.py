"""编码验证候选命令：探测、授权门禁与结构化结果。"""

import os
import tempfile
import unittest
from pathlib import Path

import super_agent.adapter.cli as cli
from super_agent.adapter.process import ProcessTools
from super_agent.adapter.tools import FatalToolError, ToolPolicy


def _project(marks: dict[str, str]) -> tuple[Path, tempfile.TemporaryDirectory]:
    """创建带指定标记文件的临时项目，并返回其根目录与清理句柄。"""
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    for name, content in marks.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content, encoding="utf-8")
    return root, directory


class VerificationCandidateTests(unittest.TestCase):
    def test_pyproject_detects_pytest_candidate(self):
        root, directory = _project({"pyproject.toml": "[project]\nname='x'\n"})
        old = os.getcwd()
        os.chdir(root)
        try:
            config = cli._load_code(None)
        finally:
            os.chdir(old)
            directory.cleanup()
        self.assertIsNotNone(config.process)
        self.assertIn(("python", "-m", "pytest"), config.process.allowed_commands)

    def test_no_marker_yields_no_process_tools(self):
        root, directory = _project({"README.md": "nothing here\n"})
        old = os.getcwd()
        os.chdir(root)
        try:
            config = cli._load_code(None)
        finally:
            os.chdir(old)
            directory.cleanup()
        self.assertIsNone(config.process)

    def test_undeclared_command_is_rejected_by_whitelist(self):
        root, directory = _project({"pyproject.toml": "[project]\nname='x'\n"})
        old = os.getcwd()
        os.chdir(root)
        try:
            config = cli._load_code(None)
        finally:
            os.chdir(old)
            directory.cleanup()
        process = ProcessTools(config.process)
        run_check = next(tool for tool in process.tools() if tool.name == "run_check")
        with self.assertRaises(PermissionError):
            run_check.handler({"command": ["python", "-c", "print(1)"]}, None)

    def test_execute_effect_requires_confirmation(self):
        root, directory = _project({"pyproject.toml": "[project]\nname='x'\n"})
        old = os.getcwd()
        os.chdir(root)
        try:
            config = cli._load_code(None)
        finally:
            os.chdir(old)
            directory.cleanup()
        process = ProcessTools(config.process)
        # 默认 allowed_effects 不含 execute，_confirm_action 返回 False => 阻止。
        policy = ToolPolicy(config.allowed_effects, lambda *a, **k: False)
        run_check = next(tool for tool in process.tools() if tool.name == "run_check")
        protected = policy.protect(run_check)
        with self.assertRaises(FatalToolError):
            protected.handler({"command": ["python", "-m", "pytest"]}, None)

    def test_declared_command_runs_and_returns_stdio(self):
        root, directory = _project({"pyproject.toml": "[project]\nname='x'\n"})
        old = os.getcwd()
        os.chdir(root)
        try:
            config = cli._load_code(None)
            process = ProcessTools(config.process)
            policy = ToolPolicy({"read", "execute"}, lambda *a, **k: True)
            run_check = next(tool for tool in process.tools() if tool.name == "run_check")
            protected = policy.protect(run_check)
            result = protected.handler({"command": ["python", "-m", "pytest"]}, None)
        finally:
            os.chdir(old)
            directory.cleanup()
        # 结果包含 argv、cwd（进程根目录）与退出状态。
        self.assertEqual(["python", "-m", "pytest"], result["command"])
        self.assertEqual(str(root.resolve()), result["cwd"])
        self.assertIn("returncode", result)


if __name__ == "__main__":
    unittest.main()
