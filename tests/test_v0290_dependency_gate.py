"""可解释轻量化：依赖方向门禁可检测核心反向导入，且核心无反向依赖。"""

import tempfile
import unittest
from pathlib import Path

from scripts.verify_release import _check_dependency_direction


class DependencyDirectionGateTests(unittest.TestCase):
    def test_core_has_no_reverse_import_of_skill_or_adapter(self):
        package = Path(__file__).resolve().parents[1] / "src" / "super_agent"
        errors = _check_dependency_direction(package)
        self.assertEqual([], errors)

    def test_gate_detects_core_reverse_importing_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "super_agent"
            core = package / "core"
            core.mkdir(parents=True)
            (core / "leak.py").write_text(
                "import super_agent.skill.organization\n", encoding="utf-8"
            )
            errors = _check_dependency_direction(package)
            self.assertTrue(any("reverse-imports skill" in item for item in errors))

    def test_gate_detects_core_reverse_importing_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "super_agent"
            core = package / "core"
            core.mkdir(parents=True)
            (core / "leak.py").write_text(
                "from super_agent.adapter.storage import MemoryStorage\n",
                encoding="utf-8",
            )
            errors = _check_dependency_direction(package)
            self.assertTrue(any("reverse-imports adapter" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
