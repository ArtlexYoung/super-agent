import tempfile
import unittest
from pathlib import Path

from adapter.repository import IncrementalRepositoryMap


class IncrementalRepositoryMapTests(unittest.TestCase):
    def test_repository_map_reuses_unchanged_files_and_refreshes_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("src").mkdir()
            first = root / "src" / "first.py"
            second = root / "src" / "second.txt"
            first.write_text("class Agent:\n    def run(self):\n        pass\n", encoding="utf-8")
            second.write_text("notes\n", encoding="utf-8")
            repository = IncrementalRepositoryMap(root, [])

            initial = repository.refresh_repository_map({})
            reused = repository.refresh_repository_map({})
            first.write_text("def changed():\n    return True\n", encoding="utf-8")
            changed = repository.refresh_repository_map({})
            second.unlink()
            deleted = repository.refresh_repository_map({})

            self.assertEqual((2, 0), (initial["refreshed"], initial["reused"]))
            self.assertEqual((0, 2), (reused["refreshed"], reused["reused"]))
            self.assertEqual((1, 1), (changed["refreshed"], changed["reused"]))
            self.assertEqual(["src/second.txt"], deleted["deleted"])
            self.assertEqual(1, deleted["file_count"])
            entry = deleted["files"][0]
            self.assertEqual("python-ast", entry["symbol_parser"])
            self.assertEqual(
                [{"name": "changed", "kind": "function", "line": 1}],
                entry["symbols"],
            )

    def test_repository_map_prunes_ignored_paths_and_reports_skips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workspace"
            root.mkdir()
            root.joinpath("node_modules").mkdir()
            root.joinpath("node_modules", "noise.js").write_text("noise", encoding="utf-8")
            root.joinpath("binary.bin").write_bytes(b"\xff")
            outside = base / "outside-map.txt"
            outside.write_text("outside", encoding="utf-8")
            root.joinpath("outside-link").symlink_to(outside)
            repository = IncrementalRepositoryMap(root, ["node_modules"])

            result = repository.refresh_repository_map({})

            self.assertEqual([], result["files"])
            self.assertEqual(
                {"binary.bin", "outside-link"},
                {item["path"] for item in result["skipped"]},
            )

    def test_repository_map_reports_python_parse_errors_without_fake_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("broken.py").write_text("def broken(:\n", encoding="utf-8")

            result = IncrementalRepositoryMap(root, []).refresh_repository_map({})
            entry = result["files"][0]

            self.assertEqual([], entry["symbols"])
            self.assertEqual("python-ast", entry["symbol_parser"])
            self.assertIn("line 1", entry["parse_error"])

    def test_repository_map_tool_is_read_only_and_explicitly_named(self) -> None:
        tool = IncrementalRepositoryMap(Path.cwd(), []).list_tools()[0]

        self.assertEqual("refresh_repository_map", tool.name)
        self.assertEqual(["read"], [effect.value for effect in tool.action.effects])


if __name__ == "__main__":
    unittest.main()
