"""损坏被动资源局部受阻；健康方法、严格身份和关键错误边界仍有效。"""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from scripts.verify_release import _check_builtin_plugins
from super_agent import Agent
from super_agent.adapter.cli import main
from super_agent.core.model import Tool
from super_agent.core.provider import MockModel
from super_agent.skill.document import format_skill
from super_agent.skill.library import AgentLibrary, ResourceUnavailableError
from tests.run_state import make_run_state
from tests.test_v0216_plugins import write_mcp, write_plugin, write_skill


def _including(root: Path, name: str, *references: str) -> Path:
    path = write_skill(root, name)
    path.write_text(format_skill({
        "name": name.rsplit("/", 1)[-1], "description": "Composed method",
        "version": "1.0.0", "includes": references,
    }, "Follow all included methods."), encoding="utf-8")
    return path


class ResourceFaultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_invalid_skill_formats_leave_healthy_neighbor_available(self):
        healthy = write_skill(self.root, "healthy", "Healthy method.")
        damaged = write_skill(self.root, "damaged")
        cases = (
            (b"No frontmatter.", "front matter"),
            (b"\xff\xfe", "decode"),
            (b"---\nname: wrong\ndescription: x\n---\nBody.", "parent directory"),
            (b"---\nname: damaged\ndescription: x\nmetadata: []\n---\nBody.", "string"),
        )
        for content, reason in cases:
            with self.subTest(reason=reason):
                damaged.write_bytes(content)
                library = AgentLibrary((self.root,))
                self.assertEqual(["skill:healthy"], [item["key"] for item in library.list_skills().items])
                self.assertEqual("Healthy method.", library.preview_skill("healthy").content)
                with self.assertRaisesRegex(ResourceUnavailableError, reason) as caught:
                    library.find_skill("damaged")
                diagnostic = caught.exception.diagnostics[0]
                self.assertEqual("skill:damaged", diagnostic.reference)
                self.assertEqual(damaged, diagnostic.path)
                self.assertEqual("invalid_format", diagnostic.code)
                self.assertEqual(64, len(diagnostic.sha256))
                self.assertTrue(healthy.is_file())

    def test_invalid_skill_metadata_is_local(self):
        write_skill(self.root, "healthy")
        damaged = write_skill(self.root, "damaged")
        metadata = damaged.parent / "skill.toml"
        for content in ("schema = [", 'schema = 2\nid = "damaged"', 'schema = 1\nid = "other"'):
            with self.subTest(content=content):
                metadata.write_text(content, encoding="utf-8")
                library = AgentLibrary((self.root,))
                self.assertEqual(1, library.list_skills().total)
                with self.assertRaises(ResourceUnavailableError):
                    library.find_skill("damaged")

    def test_transitive_skill_and_plugin_dependencies_are_explained(self):
        damaged = write_skill(self.root, "damaged")
        damaged.write_text("broken", encoding="utf-8")
        write_skill(self.root, "healthy")
        _including(self.root, "first", "skill:damaged")
        _including(self.root, "second", "skill:first")
        write_plugin(self.root, "leaf", ("second",))
        write_plugin(self.root, "outer", ("healthy",), included_plugins=("plugin:leaf",))
        write_plugin(self.root, "healthy", ("healthy",))
        library = AgentLibrary((self.root,))
        faults = {item.reference: item for item in library.snapshot().diagnostics}
        for key, dependency in (
            ("skill:first", "skill:damaged"), ("skill:second", "skill:first"),
            ("plugin:leaf", "skill:second"), ("plugin:outer", "plugin:leaf"),
        ):
            self.assertEqual("blocked_dependency", faults[key].code)
            self.assertEqual((dependency,), faults[key].dependencies)
        self.assertEqual({"healthy"}, set(library.snapshot().plugins))
        self.assertEqual({"skill:healthy"}, set(library.snapshot().skills))
        self.assertEqual(("skill:healthy",), library.activate_plugin("healthy", make_run_state()))
        with self.assertRaisesRegex(ResourceUnavailableError, "plugin:leaf"):
            library.preview_plugin("outer")

    def test_corrupt_plugin_and_required_or_optional_mcp_definitions_are_local(self):
        write_skill(self.root, "healthy")
        broken_plugin = write_plugin(self.root, "broken", ("healthy",)) / "plugin.toml"
        broken_plugin.write_text('schema = [', encoding="utf-8")
        write_plugin(self.root, "outer", ("healthy",), included_plugins=("plugin:broken",))
        broken_mcp = write_mcp(self.root, "broken") / "mcp.toml"
        broken_mcp.write_bytes(b"\xff")
        write_plugin(self.root, "required", ("healthy",), required_mcp_servers=("mcp:broken",))
        write_plugin(self.root, "optional", ("healthy",), optional_mcp_servers=("mcp:broken",))
        library = AgentLibrary((self.root,))
        self.assertEqual(1, library.list_skills().total)
        self.assertEqual(0, library.list_mcp_servers().total)
        self.assertEqual(0, library.list_plugins().total)
        for key, finder in (("broken", library.find_plugin), ("broken", library.find_mcp_server),
                            ("required", library.find_plugin), ("optional", library.find_plugin)):
            with self.assertRaises(ResourceUnavailableError):
                finder(key)
        self.assertEqual(5, library.list_diagnostics().total)

    def test_all_unavailable_direct_dependencies_are_reported(self):
        for name in ("a", "b"):
            write_skill(self.root, name).write_text("broken", encoding="utf-8")
        _including(self.root, "parent", "skill:b", "skill:a")
        diagnostic = next(item for item in AgentLibrary((self.root,)).snapshot().diagnostics
                          if item.reference == "skill:parent")
        self.assertEqual(("skill:a", "skill:b"), diagnostic.dependencies)

    def test_invalid_duplicate_source_never_falls_back_to_valid_copy(self):
        first, second = self.root / "one", self.root / "two"
        write_skill(first, "same", "Do not silently fall back.")
        write_skill(second, "same").write_text("broken", encoding="utf-8")
        for paths in ((first, second), (second, first)):
            with self.subTest(paths=paths):
                library = AgentLibrary(paths)
                self.assertEqual(0, library.list_skills().total)
                with self.assertRaises(ResourceUnavailableError):
                    library.find_skill("same")

    def test_long_dependency_diagnostic_is_bounded_without_losing_references(self):
        references = []
        for index in range(30):
            name = f"method-{index:02d}-" + "x" * 40
            write_skill(self.root, name).write_text("broken", encoding="utf-8")
            references.append(f"skill:{name}")
        _including(self.root, "parent", *references)
        library = AgentLibrary((self.root,))
        with self.assertRaises(ResourceUnavailableError) as caught:
            library.find_skill("parent")
        diagnostic = caught.exception.diagnostics[0]
        self.assertEqual(1_024, len(diagnostic.message))
        self.assertTrue(diagnostic.message.endswith("..."))
        self.assertEqual(tuple(references), diagnostic.dependencies)

    def test_corrupt_overlay_stays_unavailable_until_repaired_and_refreshed(self):
        shared, writable = self.root / "shared", self.root / "writable"
        original_path = write_skill(shared, "method", "Shared baseline.")
        library = AgentLibrary((shared,), writable_root=writable)
        original = library.find_skill("method")
        updated = library.update_skill("method", "Updated overlay.", expected_sha256=original.sha256)
        valid_content = updated.path.read_bytes()
        frozen = library.snapshot()
        updated.path.write_text("broken", encoding="utf-8")
        library.refresh()
        damaged_snapshot = library.snapshot()
        self.assertNotEqual(frozen.snapshot_id, damaged_snapshot.snapshot_id)
        self.assertEqual("Updated overlay.", frozen.skills["skill:method"].body)
        with self.assertRaises(ResourceUnavailableError):
            library.find_skill("method")
        updated.path.write_bytes(valid_content)
        with self.assertRaises(ResourceUnavailableError):
            library.find_skill("method")
        library.refresh()
        self.assertEqual("Updated overlay.", library.find_skill("method").body)
        self.assertEqual(frozen.snapshot_id, library.snapshot().snapshot_id)
        self.assertEqual((), library.snapshot().diagnostics)
        self.assertIn("Shared baseline.", original_path.read_text(encoding="utf-8"))
        write_skill(shared, "method", "Concurrent baseline change.")
        library.refresh()
        with self.assertRaisesRegex(ValueError, "identity conflicts"):
            library.snapshot()

    def test_damaged_bytes_change_snapshot_even_when_parser_error_is_identical(self):
        damaged = write_skill(self.root, "damaged")
        damaged.write_text("broken one", encoding="utf-8")
        library = AgentLibrary((self.root,))
        first = library.snapshot()
        damaged.write_text("broken two", encoding="utf-8")
        self.assertIs(first, library.snapshot())
        library.refresh()
        second = library.snapshot()
        self.assertEqual(first.diagnostics[0].message, second.diagnostics[0].message)
        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        self.assertNotEqual(first.diagnostics[0].sha256, second.diagnostics[0].sha256)

    def test_immutable_diagnostics_are_paged_through_host_and_tool(self):
        for name in ("a", "b", "c"):
            write_skill(self.root, name).write_text("broken", encoding="utf-8")
        library = AgentLibrary((self.root,))
        first = library.list_diagnostics(page_size=2)
        second = library.list_diagnostics(page=2, page_size=2)
        self.assertEqual(3, first.total)
        self.assertTrue(first.has_more)
        self.assertEqual(["skill:a", "skill:b"], [item["key"] for item in first.items])
        self.assertEqual(["skill:c"], [item["key"] for item in second.items])
        with self.assertRaises(FrozenInstanceError):
            library.snapshot().diagnostics[0].message = "changed"
        with self.assertRaises(TypeError):
            first.items[0]["message"] = "changed"
        tool = next(item for item in library.tools() if item.name == "list_resource_diagnostics")
        self.assertEqual(second.to_dict(), tool.handler({"page": 2, "page_size": 2}, None))
        for arguments in ({"page": 0}, {"page_size": 101}, {"page_size": True}):
            with self.assertRaises((ValueError, TypeError)):
                tool.handler(arguments, None)
        self.assertEqual([], library.list_diagnostics(page=4).to_dict()["items"])

    def test_explicit_or_enabled_damaged_method_never_calls_model(self):
        damaged = write_skill(self.root, "damaged")
        write_skill(self.root, "healthy", "Exact healthy instructions.")
        write_plugin(self.root, "affected", ("damaged",))
        model = MockModel("Healthy result")
        agent = Agent(model)
        agent.use_agent_library(AgentLibrary((self.root,)))
        agent.enable_skill("damaged")
        damaged.write_text("broken", encoding="utf-8")
        with self.assertRaises(ResourceUnavailableError):
            agent.run("Use the selected method")
        self.assertEqual([], model.requests)
        agent = Agent(model)
        agent.use_agent_library(AgentLibrary((self.root,)))
        for options in ({"skill": "damaged"}, {"plugin": "affected"}):
            with self.assertRaises(ResourceUnavailableError):
                agent.run("Use the exact selection", **options)
        self.assertEqual([], model.requests)
        result = agent.run("Use the healthy selection", skill="healthy")
        self.assertEqual("Healthy result", result.text)
        self.assertEqual(1, len(model.requests))
        self.assertIn("Exact healthy instructions.", str(model.requests[0].messages))
        self.assertEqual(2, len(result.library_snapshot["diagnostics"]))

    def test_missing_references_and_cycles_still_fail_even_with_unrelated_damage(self):
        write_skill(self.root, "broken").write_text("broken", encoding="utf-8")
        a = _including(self.root, "a", "skill:missing")
        with self.assertRaisesRegex(ValueError, "include is missing"):
            AgentLibrary((self.root,)).snapshot()
        _including(self.root, "a", "skill:b", "skill:broken")
        _including(self.root, "b", "skill:a")
        with self.assertRaisesRegex(ValueError, "Skill include cycle"):
            AgentLibrary((self.root,)).snapshot()
        a.write_text(format_skill({"name": "a", "description": "a"}, "a"), encoding="utf-8")
        _including(self.root, "b", "skill:a")
        write_plugin(self.root, "a", ("a",), included_plugins=("plugin:b",))
        write_plugin(self.root, "b", ("b",), included_plugins=("plugin:a",))
        with self.assertRaisesRegex(ValueError, "plugin include cycle"):
            AgentLibrary((self.root,)).snapshot()

    def test_identity_conflict_and_invalid_directory_are_not_isolated(self):
        first, second = self.root / "one", self.root / "two"
        write_skill(first, "same", "First")
        write_skill(second, "same", "Second")
        write_skill(first, "broken").write_text("broken", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "identity conflicts"):
            AgentLibrary((first, second)).snapshot()
        write_skill(self.root, "invalid.directory").write_text("broken", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid skill ID"):
            AgentLibrary((self.root,)).snapshot()

    def test_read_scan_and_record_failures_are_not_downgraded_to_diagnostics(self):
        write_skill(self.root, "healthy")
        library = AgentLibrary((self.root,))
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                library.snapshot()

        def denied_walk(root, *, onerror):
            onerror(PermissionError("scan denied"))
            return iter(())

        with patch("super_agent.skill.library.os.walk", side_effect=denied_walk):
            with self.assertRaisesRegex(PermissionError, "scan denied"):
                library.snapshot()
        self.assertEqual((), library.snapshot().diagnostics)
        library.refresh()
        write_skill(self.root, "broken").write_text("broken", encoding="utf-8")

        def record_failure(event, data):
            raise OSError("record unavailable")

        library.record_event = record_failure
        with self.assertRaisesRegex(OSError, "record unavailable"):
            library.snapshot()
        events = []
        library.record_event = lambda event, data: events.append((event, data))
        self.assertEqual(1, library.list_diagnostics().total)
        library.list_diagnostics()
        self.assertEqual(["skill.unavailable"], [event for event, data in events])

    @unittest.skipIf(os.geteuid() == 0, "root bypasses filesystem permissions")
    def test_real_unreadable_directory_is_not_reported_as_empty(self):
        path = write_skill(self.root, "unreadable")
        write_skill(self.root, "healthy")
        library = AgentLibrary((self.root,))
        path.parent.chmod(0)
        try:
            with self.assertRaises(PermissionError):
                library.snapshot()
        finally:
            path.parent.chmod(0o700)
        self.assertEqual(2, library.list_skills().total)
        self.assertEqual((), library.snapshot().diagnostics)

    def test_failed_activation_preserves_session_and_healthy_work_still_runs(self):
        write_skill(self.root, "broken").write_text("broken", encoding="utf-8")
        write_skill(self.root, "healthy")
        _including(self.root, "affected", "skill:broken")
        tool = Tool("marker", "Marker", lambda _arguments, _context: {"ok": True})
        session = make_run_state(instructions=("Initial instruction",), active_tools=(tool,))
        library = AgentLibrary((self.root,))
        state = (list(session.instructions), dict(session.tools), list(session.active_skills), session.context_characters)
        with self.assertRaises(ResourceUnavailableError):
            library.activate_skill("affected", session)
        self.assertEqual(state, (session.instructions, session.tools, session.active_skills, session.context_characters))
        self.assertEqual(("skill:healthy",), library.activate_skill("healthy", session))

    def test_creation_and_install_do_not_write_over_unavailable_identity(self):
        shared, writable, source = self.root / "shared", self.root / "writable", self.root / "source"
        write_skill(shared, "same").write_text("broken", encoding="utf-8")
        write_skill(shared, "healthy")
        write_plugin(shared, "same", ("healthy",))
        (shared / "plugins" / "same" / "plugin.toml").write_text("schema = [", encoding="utf-8")
        package = write_skill(source, "same")
        library = AgentLibrary((shared,), writable_root=writable)
        for action in (
            lambda: library.create_skill("same", "New", description="New"),
            lambda: library.install_skill(package),
            lambda: library.create_plugin("same", description="New", entry_skill="healthy"),
        ):
            with self.assertRaises(ResourceUnavailableError):
                action()
            self.assertFalse(writable.exists())
        with self.assertRaisesRegex(ValueError, "already exists"):
            library.create_skill("healthy", "Duplicate", description="Duplicate")
        self.assertFalse(writable.exists())

    def test_removal_needs_complete_reference_information(self):
        library = AgentLibrary((), writable_root=self.root)
        method = library.create_skill("healthy", "Healthy", description="Healthy")
        plugin = library.create_plugin("healthy", description="Healthy", entry_skill="healthy")
        write_skill(self.root, "broken").write_text("broken", encoding="utf-8")
        library.refresh()
        with self.assertRaisesRegex(RuntimeError, "clean library"):
            library.remove_skill("healthy", expected_sha256=method.sha256)
        with self.assertRaisesRegex(RuntimeError, "clean library"):
            library.remove_plugin("healthy", expected_sha256=plugin.sha256)
        self.assertTrue(method.path.is_file())
        self.assertTrue((plugin.root / "plugin.toml").is_file())

    def test_cli_check_reports_paged_faults_with_nonzero_exit_then_recovers(self):
        damaged = write_skill(self.root, "damaged")
        write_skill(self.root, "healthy")
        write_plugin(self.root, "affected", ("damaged",))
        damaged.write_text("broken", encoding="utf-8")
        config = self.root / "common.toml"
        config.write_text(
            f'library_paths = [{json.dumps(str(self.root))}]\n'
            'models = [{ name = "offline", provider = "mock", model = "ok", description = "Offline" }]\n',
            encoding="utf-8",
        )
        for page in (1, 2):
            output, errors = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                status = main(["check", "--config", str(config), "--output", "json",
                               "--page", str(page), "--page-size", "1"])
            result = json.loads(output.getvalue())
            self.assertEqual(1, status, errors.getvalue())
            self.assertTrue(result["model_ready"])
            self.assertFalse(result["resources_ready"])
            self.assertEqual(2, result["resource_diagnostics"]["total"])
            self.assertEqual(1, len(result["resource_diagnostics"]["items"]))
        write_skill(self.root, "damaged")
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["check", "--config", str(config), "--output", "json"])
        self.assertEqual(0, status)
        self.assertTrue(json.loads(output.getvalue())["resources_ready"])

    def test_release_gate_rejects_any_quarantined_builtin(self):
        source = Path(__file__).resolve().parents[1] / "src" / "super_agent" / "skill" / "builtin"
        target = self.root / "builtin"
        shutil.copytree(source, target)
        (target / "skills" / "super-agent" / "common" / "SKILL.md").write_bytes(b"\xff")
        errors = _check_builtin_plugins(target)
        self.assertTrue(any("resource is unavailable: skill:super-agent/common" in error for error in errors))
        self.assertTrue(any("cannot read builtin Skill" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
