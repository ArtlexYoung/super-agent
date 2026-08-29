import tempfile
import unittest
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from adapter.storage import MemoryStorage
from core.config import EvolutionConfig, config_from_dict
from core.event import RunIdentity
from core.model import ModelEvent, Tool
from core.provider import MockModel
from core.records import EventStore
from core.run import RunSession, ToolContext
from skill.document import format_skill, parse_skill_text, read_skill_package
from skill.evolution import (
    SkillEvidence,
    SkillEvolution,
    SkillTestCase,
    calculate_freshness,
)
from skill.library import AgentLibrary
from super_agent import Agent


def write_skill(
    root: Path,
    skill_id: str,
    body: str = "Use the method.",
    *,
    requires: tuple[str, ...] = (),
    optional_tools: tuple[str, ...] = (),
) -> Path:
    path = root / "skills" / skill_id / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        format_skill(
            {
                "name": skill_id.rsplit("/", 1)[-1],
                "description": "A reusable method",
                "version": "1.0.0",
                "requires": requires,
                "optional_tools": optional_tools,
            },
            body,
        ),
        encoding="utf-8",
    )
    return path


def write_plugin(
    root: Path,
    plugin_id: str,
    skills: tuple[str, ...],
    *,
    included_plugins: tuple[str, ...] = (),
    required_mcp_servers: tuple[str, ...] = (),
    optional_mcp_servers: tuple[str, ...] = (),
) -> Path:
    path = root / "plugins" / plugin_id
    path.mkdir(parents=True, exist_ok=True)
    path.joinpath("plugin.toml").write_text(
        "\n".join(
            (
                "schema = 1",
                f'id = "{plugin_id}"',
                'version = "1.0.0"',
                'description = "A reference-only plugin"',
                f'entry_skill = "skill:{skills[0]}"',
                f"skills = {list(skills[1:])!r}".replace("'", '"'),
                f"included_plugins = {list(included_plugins)!r}".replace("'", '"'),
                f"required_mcp_servers = {list(required_mcp_servers)!r}".replace("'", '"'),
                f"optional_mcp_servers = {list(optional_mcp_servers)!r}".replace("'", '"'),
                "",
            )
        ),
        encoding="utf-8",
    )
    return path


def write_mcp(root: Path, mcp_id: str = "example/search") -> Path:
    path = root / "mcps" / mcp_id
    path.mkdir(parents=True, exist_ok=True)
    path.joinpath("mcp.toml").write_text(
        f'schema = 1\nid = "{mcp_id}"\nversion = "1.0.0"\n'
        'description = "Search description"\ntools = ["search"]\n',
        encoding="utf-8",
    )
    return path


class SearchServer:
    def list_tools(self):
        return (
            {
                "name": "search",
                "description": "Search",
                "inputSchema": {"type": "object", "properties": {}},
            },
        )

    def call_tool(self, name, arguments):
        return {"name": name, "arguments": dict(arguments)}


class AgentLibraryTests(unittest.TestCase):
    def test_standard_skill_metadata_and_invalid_front_matter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "skills" / "external-method" / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(
                "---\n"
                "name: external-method\n"
                "description: 'External standard method.' # selection text\n"
                "metadata:\n"
                "  author: 'example-org' # standard metadata\n"
                '  version: "1.0"\n'
                "---\n\n"
                "Follow the external method.\n",
                encoding="utf-8",
            )
            skill = AgentLibrary((root,)).find_skill("skill:external-method")
            self.assertEqual("example-org", skill.metadata["standard_metadata"]["author"])

            legacy = '+++\nname = "legacy"\ndescription = "old"\n+++\nbody\n'
            with self.assertRaisesRegex(ValueError, "YAML front matter"):
                parse_skill_text(legacy, Path("legacy") / "SKILL.md")
            standard = "---\nname: right-name\ndescription: Correct format\n---\nbody\n"
            with self.assertRaisesRegex(ValueError, "parent directory"):
                parse_skill_text(standard, Path("wrong-name") / "SKILL.md")

    def test_skill_packages_preserve_resources_and_reject_unsafe_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            skill_path = write_skill(source_root, "demo")
            reference = skill_path.parent / "references" / "method.md"
            reference.parent.mkdir()
            reference.write_text("evidence-" * 4, encoding="utf-8")
            source = AgentLibrary((source_root,))

            direct = AgentLibrary((), writable_root=root / "direct")
            installed_direct = direct.install_skill(skill_path)
            self.assertEqual(
                "evidence-" * 4,
                (installed_direct.root / "references" / "method.md").read_text(
                    encoding="utf-8"
                ),
            )

            archive = source.pack_skill("skill:demo", root / "demo.zip")
            installed = AgentLibrary((), writable_root=root / "installed")
            skill = installed.install_skill(archive)
            tool = next(
                item for item in installed.tools() if item.name == "read_skill_resource"
            )
            session = RunSession(RunIdentity(), [], [], {})
            context = ToolContext(session, lambda _event, _data: None)  # type: ignore[arg-type]
            page = tool.handler(
                {
                    "skill": "skill:demo",
                    "path": "references/method.md",
                    "max_characters": 9,
                },
                context,
            )
            self.assertEqual("evidence-", page["content"])
            self.assertIsNotNone(page["next_offset"])
            with self.assertRaises(PermissionError):
                tool.handler(
                    {"skill": "skill:demo", "path": "../outside.md"}, context
                )
            installed.remove_skill(skill.reference, expected_sha256=skill.sha256)
            self.assertFalse(skill.root.exists())

            nested = skill_path.parent / "references" / "nested" / "SKILL.md"
            nested.parent.mkdir(parents=True)
            nested.write_text(skill_path.read_text(encoding="utf-8"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot contain nested Skills"):
                read_skill_package(skill_path)

            content = skill_path.read_text(encoding="utf-8")
            duplicate = root / "duplicate.zip"
            with zipfile.ZipFile(duplicate, "w") as package:
                package.writestr("demo/SKILL.md", content)
                package.writestr("demo/references/result.md", "first")
                package.writestr("demo/references/./result.md", "second")
            with self.assertRaisesRegex(ValueError, "duplicate paths"):
                read_skill_package(duplicate)
            limited = root / "limited.zip"
            with zipfile.ZipFile(limited, "w") as package:
                package.writestr("demo/SKILL.md", content)
                package.writestr("demo/references/result.md", "result")
            with (
                patch("skill.document.MAX_PACKAGE_FILES", 1),
                self.assertRaisesRegex(ValueError, "more than 1 files"),
            ):
                read_skill_package(limited)
            with (
                patch("skill.document.MAX_PACKAGE_BYTES", 10),
                self.assertRaisesRegex(ValueError, "unpacked bytes"),
            ):
                read_skill_package(limited)

    def test_disclosure_cache_history_and_activation_share_the_library(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(root, "demo", "A" * 30)
            library = AgentLibrary((root,), cache_root=root / "cache")
            page = library.list_skills(page=1, page_size=1)
            self.assertEqual("skill:demo", page.items[0]["key"])
            first = library.disclose_skill("skill:demo", max_characters=10)
            cached = library.read_disclosed(first.cache_path, max_characters=10)
            self.assertEqual((10, first.content), (len(first.content), cached.content))
            self.assertEqual(2, len(library.history()))
            session = RunSession(
                RunIdentity(), [], [], {}, values={"available_tools": {}}
            )
            self.assertEqual(
                ("skill:demo",), library.activate_skill("skill:demo", session)
            )
            self.assertIn("AAAAAAAA", session.instructions[0])

    def test_memory_disclosure_cache_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(root, "demo", "abcdef")
            write_skill(root, "other", "uvwxyz")
            library = AgentLibrary((root,), cache_entries=1)
            first = library.disclose_skill("skill:demo", max_characters=2)
            second = library.disclose_skill("skill:other", max_characters=2)
            self.assertTrue(second.cache_path.startswith("memory://"))
            self.assertEqual(second.cache_path, library.history()[0]["cache_path"])
            with self.assertRaises(KeyError):
                library.read_disclosed(first.cache_path)

    def test_disabled_skill_is_hidden_and_activation_is_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(root, "hidden")
            disabled = AgentLibrary((root,), disabled_skills=("skill:hidden",))
            self.assertEqual(0, disabled.list_skills().total)
            with self.assertRaises(PermissionError):
                disabled.find_skill("skill:hidden")

            write_skill(
                root,
                "blocked",
                "Needs two tools.",
                requires=("available", "missing"),
            )
            library = AgentLibrary((root,))
            available = Tool("available", "Available", lambda _args, _context: {})
            session = RunSession(
                RunIdentity(),
                [],
                ["existing"],
                {},
                values={"available_tools": {"available": available}},
                context_characters=8,
            )
            with self.assertRaises(RuntimeError):
                library.activate_skill("skill:blocked", session)
            self.assertEqual(({}, ["existing"], [], 8), (
                session.tools,
                session.instructions,
                session.active_skills,
                session.context_characters,
            ))

    def test_skill_tools_mount_only_when_declared_and_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(
                root,
                "optional",
                requires=("required",),
                optional_tools=("optional", "not_registered"),
            )
            required = Tool("required", "Required", lambda _args, _context: {})
            optional = Tool("optional", "Optional", lambda _args, _context: {})
            session = RunSession(
                RunIdentity(),
                [],
                [],
                {},
                values={"available_tools": {"required": required, "optional": optional}},
            )
            library = AgentLibrary((root,))
            library.activate_skill("skill:optional", session)
            self.assertEqual({"required", "optional"}, set(session.tools))
            self.assertEqual(
                ["optional", "not_registered"],
                library.list_skills().items[0]["optional_tools"],
            )

    def test_central_library_discovers_independent_skills_and_reference_only_plugins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(root, "example/common")
            write_skill(root, "example/review")
            write_plugin(root, "example/code", ("example/common", "example/review"))
            library = AgentLibrary((root,))

            snapshot = library.snapshot()
            self.assertEqual({"skill:example/common", "skill:example/review"}, set(snapshot.skills))
            plugin = library.find_plugin("plugin:example/code")
            self.assertEqual("skill:example/common", plugin.entry_skill)
            self.assertEqual(("skill:example/review",), plugin.skills)

    def test_identical_resources_are_reused_and_conflicts_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = base / "first"
            second = base / "second"
            write_skill(first, "example/common", "same")
            write_skill(second, "example/common", "same")
            library = AgentLibrary((first, second))
            skill = library.find_skill("skill:example/common")
            self.assertEqual(2, len(skill.sources))

            write_skill(second, "example/common", "different")
            library.refresh()
            with self.assertRaisesRegex(ValueError, "Skill identity conflicts"):
                library.snapshot()

    def test_mcp_is_passive_and_plugins_require_explicit_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_mcp(root)
            write_skill(root, "example/task")
            write_plugin(
                root,
                "example/task",
                ("example/task",),
                required_mcp_servers=("mcp:example/search",),
            )
            library = AgentLibrary((root,))
            self.assertEqual(("search",), library.find_mcp_server("mcp:example/search").tools)
            session = RunSession(
                RunIdentity(), [], [], {}, values={"available_tools": {}, "mcp_tools_by_server": {}}
            )
            with self.assertRaisesRegex(RuntimeError, "explicitly connected"):
                library.activate_plugin("plugin:example/task", session)

    def test_mcp_tools_are_mounted_only_when_the_referencing_plugin_is_active(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_mcp(root)
            write_skill(root, "example/task")
            write_plugin(
                root,
                "example/task",
                ("example/task",),
                required_mcp_servers=("mcp:example/search",),
            )
            model = MockModel()
            agent = Agent(model)
            agent.use_agent_library(AgentLibrary((root,)))
            agent.connect_mcp_server(
                "mcp:example/search", SearchServer(), effects={"search": ("read",)}
            )

            agent.run("Do not activate a plugin")
            self.assertNotIn(
                "mcp_example_search_search",
                {tool.name for tool in model.requests[-1].tools},
            )
            agent.run("Use the task", plugin="plugin:example/task")
            self.assertIn(
                "mcp_example_search_search",
                {tool.name for tool in model.requests[-1].tools},
            )

    def test_included_plugin_mounts_mcp_and_optional_absence_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_mcp(root)
            write_skill(root, "example/base")
            write_skill(root, "example/task")
            write_plugin(
                root,
                "example/base",
                ("example/base",),
                required_mcp_servers=("mcp:example/search",),
            )
            write_plugin(
                root,
                "example/task",
                ("example/task",),
                included_plugins=("plugin:example/base",),
                optional_mcp_servers=("mcp:example/search",),
            )
            events = []
            library = AgentLibrary((root,), record_event=lambda event, data: events.append((event, data)))
            tool = Tool("search", "Search", lambda _arguments, _context: {})
            session = RunSession(
                RunIdentity(),
                [],
                [],
                {},
                values={
                    "available_tools": {},
                    "mcp_tools_by_server": {"mcp:example/search": (tool,)},
                },
            )
            library.activate_plugin("plugin:example/task", session)
            self.assertIn("search", session.tools)

            write_mcp(root, "example/optional")
            write_skill(root, "example/optional")
            write_plugin(
                root,
                "example/optional",
                ("example/optional",),
                optional_mcp_servers=("mcp:example/optional",),
            )
            library.refresh()
            library.activate_plugin("plugin:example/optional", session)
            unavailable = [data for event, data in events if event == "mcp.unavailable"]
            self.assertEqual("mcp:example/optional", unavailable[-1]["key"])

    def test_skill_override_is_central_and_hash_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            write_skill(source, "example/task", "old")
            library = AgentLibrary((source,), writable_root=root / "owned")
            original = library.find_skill("skill:example/task")
            with self.assertRaises(RuntimeError):
                library.update_skill(
                    original.reference, "stale", expected_sha256="0" * 64
                )
            self.assertFalse(
                (root / "owned" / "skills" / "example" / "task").exists()
            )
            updated = library.update_skill(
                original.reference, "new", expected_sha256=original.sha256
            )
            self.assertEqual("new", updated.body)
            self.assertTrue((root / "owned" / "skills" / "example" / "task" / "skill.toml").is_file())
            with self.assertRaises(RuntimeError):
                library.update_skill(
                    original.reference, "stale", expected_sha256=original.sha256
                )
            self.assertEqual("old", AgentLibrary((source,)).find_skill(original.reference).body)

    def test_plugin_manifest_rejects_ambiguous_or_missing_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(root, "example/task")
            write_plugin(root, "example/task", ("example/task",))
            manifest = root / "plugins" / "example" / "task" / "plugin.toml"
            original = manifest.read_text(encoding="utf-8")

            manifest.write_text(
                original.replace(
                    "skills = []", 'skills = ["skill:example/task"]'
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must not be repeated"):
                AgentLibrary((root,)).snapshot()

            manifest.write_text(
                original.replace(
                    "optional_mcp_servers = []",
                    'optional_mcp_servers = ["mcp:missing/server"]',
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "MCP definitions are missing"):
                AgentLibrary((root,)).snapshot()

            manifest.write_text(
                original + f'base_hash = "{"0" * 64}"\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "unknown plugin manifest"):
                AgentLibrary((root,)).snapshot()

    def test_plugin_and_skill_removal_respect_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = AgentLibrary((), writable_root=Path(directory) / "owned")
            skill = library.create_skill("example/task", "task", description="Task")
            library.create_plugin(
                "example/plugin",
                description="Plugin",
                entry_skill=skill.reference,
            )
            with self.assertRaisesRegex(RuntimeError, "still referenced"):
                library.remove_skill(skill.reference, expected_sha256=skill.sha256)

    def test_builtin_scene_uses_central_paths_and_activates(self):
        root = Path(__file__).resolve().parents[1] / "src" / "skill" / "builtin"
        library = AgentLibrary((root,))
        self.assertEqual(16, library.list_skills().total)
        session = RunSession(
                RunIdentity(), [], [], {}, values={"available_tools": {"calculate_numbers": object()}}
        )
        with self.assertRaises(RuntimeError):
            library.activate_plugin("plugin:super-agent/common", session)

    def test_evolution_uses_skill_reference_and_central_write_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = AgentLibrary((), writable_root=root)
            skill = library.create_skill("local/self", "old", description="Self method")
            policy = config_from_dict({"evolution": {"allow": [skill.reference], "auto_apply": [skill.reference]}}).evolution
            evolution = SkillEvolution(library, policy=policy, runner=lambda body, _prompt: body)
            change = evolution.propose(skill.reference, "new", reason="measured")
            evolution.test(change.change_id, [SkillTestCase("new", "run", required_text=("new",))])
            evolution.apply(change.change_id)
            library.refresh()
            self.assertEqual("new", library.find_skill(skill.reference).body)

    def test_plugin_evolution_authority_covers_every_shared_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            library = AgentLibrary((), writable_root=Path(directory))
            skill = library.create_skill("local/shared", "old", description="Shared")
            library.create_plugin(
                "local/first", description="First", entry_skill=skill.reference
            )
            library.create_plugin(
                "local/second", description="Second", entry_skill=skill.reference
            )
            evolution = SkillEvolution(
                library,
                policy=EvolutionConfig(
                    ("plugin:local/second",), ("plugin:local/second",)
                ),
                runner=lambda body, _prompt: body,
            )
            change = evolution.propose(skill.reference, "new", reason="measured")
            evolution.test(
                change.change_id,
                [SkillTestCase("new", "run", required_text=("new",))],
            )
            evolution.apply(change.change_id)

    def test_evolution_allow_does_not_imply_auto_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            library = AgentLibrary((), writable_root=Path(directory))
            skill = library.create_skill("local/self", "old", description="Self")
            evolution = SkillEvolution(
                library,
                policy=EvolutionConfig((skill.reference,), ()),
                runner=lambda body, _prompt: body,
            )
            change = evolution.propose(skill.reference, "new", reason="measured")
            evolution.test(
                change.change_id,
                [SkillTestCase("new", "run", required_text=("new",))],
            )
            with self.assertRaisesRegex(PermissionError, "auto-apply"):
                evolution.apply(change.change_id)

    def test_freshness_is_deterministic_and_multidimensional(self):
        now = datetime.now(UTC)
        evidence = (
            SkillEvidence(
                "skill:demo",
                1.0,
                True,
                input_tokens=10,
                output_tokens=5,
                used_at=(now - timedelta(days=1)).isoformat(),
            ),
            SkillEvidence(
                "skill:demo",
                0.0,
                False,
                input_tokens=10_000,
                output_tokens=10_000,
                replacement_calls=2,
                used_at=(now - timedelta(days=60)).isoformat(),
            ),
        )
        first = calculate_freshness(evidence, now=now)
        self.assertEqual(first, calculate_freshness(evidence, now=now))
        self.assertEqual(2, first.sample_count)
        self.assertLess(first.quality, 1)
        self.assertLess(first.efficiency, 1)

    def test_evolution_requires_tests_can_undo_and_rebuild_persisted_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = AgentLibrary((), writable_root=root / "library")
            skill = library.create_skill(
                "local/self", "old method", description="Self method"
            )
            library.create_plugin(
                "local/self", description="Self plugin", entry_skill=skill.reference
            )
            store = EventStore(MemoryStorage(), "alice", "agent")
            policy = EvolutionConfig(
                ("plugin:local/self",), ("plugin:local/self",)
            )
            evolution = SkillEvolution(
                library,
                policy=policy,
                store=store,
                runner=lambda body, _prompt: body,
            )
            change = evolution.propose(
                skill.reference, "new method", reason="measured improvement"
            )
            with self.assertRaises(ValueError):
                evolution.apply(change.change_id)
            tested = evolution.test(
                change.change_id,
                [SkillTestCase("contains", "use", required_text=("new",))],
            )
            applied = evolution.apply(tested.change_id)
            records = store.read("skill_change", change.change_id)
            self.assertIn("candidate_body", records[0].data)
            self.assertTrue(
                all("candidate_body" not in record.data for record in records[1:])
            )
            library.refresh()
            rebuilt = SkillEvolution(
                library,
                policy=policy,
                store=store,
                runner=lambda body, _prompt: body,
            )
            undone = rebuilt.undo(applied.change_id)
            library.refresh()
            self.assertEqual("old method", library.find_skill(skill.reference).body)
            self.assertEqual("undone", undone.status)
            rebuilt.record_evidence(SkillEvidence(skill.reference, 1.0, True))
            rebuilt.record_evidence(SkillEvidence(skill.reference, 0.2, False))
            self.assertEqual((2, 1), rebuilt.count_skill_evidence(skill.reference))

    def test_skill_change_tool_event_is_linked_to_run_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(root, "local/self")
            builtin = Path(__file__).resolve().parents[1] / "src" / "skill" / "builtin"
            library = AgentLibrary(
                (root, builtin), writable_root=root / "owned"
            )
            model = MockModel(
                responses=(
                    (
                        ModelEvent.call(
                            "change-1",
                            "propose_skill_update",
                            {
                                "skill": "skill:local/self",
                                "candidate_body": "Improved method.",
                                "reason": "Measured improvement.",
                            },
                        ),
                        ModelEvent.done(),
                    ),
                    "Proposal recorded.",
                )
            )
            agent = Agent(model)
            agent.use_agent_library(library)
            agent.enable_skill_evolution()
            agent.allow_skill_to_evolve("skill:local/self")
            agent.use_storage(MemoryStorage())
            result = agent.run(
                "Improve the Skill",
                skill="skill:super-agent/evolution/self-update",
            )
            insight = agent.for_user("local").runs.explain(result.run_id)
            self.assertEqual(
                ["skill_change.proposed"],
                [item["event_type"] for item in insight["evolution"]],
            )
            self.assertTrue(insight["evolution"][0]["reason"]["redacted"])

    def test_configuration_uses_only_central_library_names(self):
        config = config_from_dict(
            {
                "library_paths": ["library"],
                "enabled_plugins": ["plugin:example/demo"],
                "enabled_mcp_servers": ["mcp:example/search"],
                "evolution": {
                    "allow": ["skill:example/demo"],
                    "auto_apply": [],
                },
            }
        )
        self.assertEqual(("library",), config.library_paths)
        self.assertEqual(("mcp:example/search",), config.enabled_mcp_servers)
        with self.assertRaisesRegex(ValueError, "subset"):
            EvolutionConfig((), ("plugin:example/demo",))


if __name__ == "__main__":
    unittest.main()
