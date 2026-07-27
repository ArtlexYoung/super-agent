import tempfile
import unittest
from pathlib import Path

from runtime.evaluation import (
    EvaluationResult,
    EvaluationSource,
    EvaluationTokenUsage,
    create_evaluation_record,
)
from runtime.store import create_local_runtime_store
from skill.disclosure import ProgressiveDisclosureCore
from skill.revision import SkillRevision


class ProgressiveDisclosureCoreTests(unittest.TestCase):
    def test_index_contains_every_skill_kind_with_stable_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_all_skill_kinds(root)

            core = _create_core(root)
            index = core.prepare_skill_index()

            self.assertEqual(
                ["mcp:filesystem", "memory:default", "prompt:echo", "workflow:direct"],
                [entry.reference.key for entry in index.entries],
            )
            self.assertTrue(core.cache_root.joinpath("index.json").is_file())

    def test_same_name_in_different_kinds_requires_explicit_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "default", triggers=["default"])
            _write_memory_skill(root, "default")
            core = _create_core(root)
            core.prepare_skill_index()

            with self.assertRaisesRegex(ValueError, "ambiguous skill name default"):
                core.open_skill("default")

            manifest = core.open_skill("default", expected_capability="memory").read_manifest()
            self.assertEqual("memory", manifest.capability)

    def test_validation_rejects_name_that_cannot_form_stable_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "invalid:name")

            issues = _create_core(root).validate_skill_sources()

            self.assertEqual(1, len(issues))
            self.assertIn("skill name must use lowercase", issues[0].message)

    def test_selection_resolves_dependencies_in_topological_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "http", provides=["http"])
            _write_prompt_skill(
                root,
                "research",
                triggers=["research"],
                provides=["facts"],
                requires=["http"],
            )
            core = _create_core(root)
            core.prepare_skill_index()

            selected = core.select_skill_references_for_prompt(
                "research this topic",
                enabled_names=[],
                allowed_capabilities={"prompt", "mcp"},
            )

            self.assertEqual(["prompt:http", "prompt:research"], [item.key for item in selected])

    def test_selection_ignores_configured_skill_disabled_by_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mcp_skill(root, "filesystem")
            core = ProgressiveDisclosureCore(
                [root / "skills"],
                create_local_runtime_store(root / "state"),
                disabled_names=["mcp"],
            )
            core.prepare_skill_index()

            selected = core.select_skill_references_for_prompt(
                "filesystem",
                enabled_names=["filesystem"],
                allowed_capabilities={"prompt", "mcp"},
            )

            self.assertEqual([], selected)

    def test_bare_name_still_selects_enabled_kind_when_other_kind_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "shared")
            _write_mcp_skill(root, "shared")
            core = ProgressiveDisclosureCore(
                [root / "skills"],
                create_local_runtime_store(root / "state"),
                disabled_names=["mcp:shared"],
            )
            core.prepare_skill_index()

            selected = core.select_skill_references_for_prompt(
                "unrelated",
                enabled_names=["shared"],
                allowed_capabilities={"prompt", "mcp"},
            )

            self.assertEqual(["prompt:shared"], [item.key for item in selected])

    def test_disclosure_stages_share_cache_and_record_cache_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "echo", triggers=["echo"], instruction="Answer briefly.")
            core = _create_core(root)
            core.prepare_skill_index()
            skill = core.open_skill("echo", expected_capability="prompt")

            manifest = skill.read_manifest()
            first = skill.read_instructions()
            second = skill.read_instructions()
            configuration = skill.read_configuration()
            history = core.read_disclosure_history()

            self.assertEqual("echo", manifest.name)
            self.assertEqual("Answer briefly.", first.content)
            self.assertEqual(first.cache_path, second.cache_path)
            self.assertEqual({}, configuration.content)
            self.assertEqual(
                ["index", "manifest", "instructions", "instructions", "configuration"],
                [event.stage for event in history],
            )
            self.assertFalse(history[2].cache_hit)
            self.assertTrue(history[3].cache_hit)
            self.assertEqual(first.content, core.read_disclosed_content(first.cache_path))

    def test_selected_skill_discloses_complete_directory_through_one_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "echo", instruction="Answer briefly.")
            skill_root = root / "skills" / "echo"
            (skill_root / "resources").mkdir()
            (skill_root / "resources" / "example.txt").write_text(
                "example content",
                encoding="utf-8",
            )
            (skill_root / "resources" / "image.bin").write_bytes(b"\xff\x00")
            core = _create_core(root)
            index = core.prepare_skill_index()

            disclosed = core.open_skill("echo", "prompt").read_skill_files()

            files = {item.relative_path: item for item in disclosed.files}
            self.assertEqual("example content", files["resources/example.txt"].content)
            self.assertIsNone(files["resources/image.bin"].content)
            self.assertEqual(index.entries[0].files_cache_path, disclosed.cache_path)
            self.assertEqual("files", core.read_disclosure_history()[-1].stage)

    def test_read_disclosed_content_rejects_path_outside_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core = _create_core(root)
            core.prepare_skill_index()
            outside = root / "outside.txt"
            outside.write_text("outside", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "outside disclosure cache"):
                core.read_disclosed_content(outside)

    def test_disclosure_rejects_skill_changed_after_index_was_prepared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "echo", instruction="Original instructions.")
            core = _create_core(root)
            core.prepare_skill_index()
            opened = core.open_skill("echo", "prompt")
            (root / "skills" / "echo" / "SKILL.md").write_text(
                "Changed instructions.",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "skill content changed after the index was prepared: prompt:echo",
            ):
                opened.read_instructions()

    def test_configuration_is_optional_for_every_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "skills" / "memory" / "broken"
            skill_dir.mkdir(parents=True)
            _write_manifest(skill_dir, "broken", "memory")

            core = _create_core(root)
            issues = core.validate_skill_sources()
            core.prepare_skill_index()

            self.assertEqual([], issues)
            self.assertEqual({}, core.open_skill("broken", "memory").read_configuration().content)

    def test_validation_rejects_instruction_path_outside_skill_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "skills" / "broken"
            skill_dir.mkdir(parents=True)
            _write_manifest(skill_dir, "broken", "prompt", include_entry=True)
            manifest_path = skill_dir / "skill.toml"
            manifest_path.write_text(
                manifest_path.read_text(encoding="utf-8").replace(
                    'instructions = "SKILL.md"',
                    'instructions = "../outside.md"',
                ),
                encoding="utf-8",
            )

            issues = _create_core(root).validate_skill_sources()

            self.assertEqual(1, len(issues))
            self.assertIn("leaves skill directory", issues[0].message)

    def test_kind_factories_only_accept_center_disclosure(self) -> None:
        from skill.kinds.mcp import create_mcp_server_from_skill_disclosure
        from skill.kinds.memory import create_memory_from_skill_disclosure
        from skill.kinds.workflow import create_workflow_policy_from_skill

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_all_skill_kinds(root)
            core = _create_core(root)
            core.prepare_skill_index()

            server = create_mcp_server_from_skill_disclosure(
                core.open_skill("filesystem", expected_capability="mcp")
            )
            memory = create_memory_from_skill_disclosure(
                core.open_skill("default", expected_capability="memory"),
                core.store,
            )
            workflow = create_workflow_policy_from_skill(
                core.open_skill("direct", expected_capability="workflow")
            )

            self.assertEqual("example-mcp", server.command)
            self.assertEqual("agent", memory.policy.default_scope)
            self.assertEqual("direct", workflow.mode)

    def test_index_centrally_merges_runtime_freshness_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "research", triggers=["research"])
            store = create_local_runtime_store(root / "state")
            store.append_evaluation_records(
                [
                    create_evaluation_record(
                        revision=SkillRevision(
                            key="prompt:research",
                            capability="prompt",
                            name="research",
                            version="0.1.0",
                            content_sha256="a" * 64,
                            function_group="research",
                            agent_created=True,
                            agent_can_update=True,
                            evolution_supported=True,
                            freshness=70.0,
                        ),
                        source=EvaluationSource(
                            source_type="agent_run",
                            run_id="run-1",
                        ),
                        result=EvaluationResult(
                            success=True,
                            score=1.0,
                            token_usage=EvaluationTokenUsage(10, 10),
                            latency_ms=10,
                            error_type="",
                            checks=["pass:run_completed"],
                        ),
                    )
                ]
            )

            entry = ProgressiveDisclosureCore(
                [root / "skills"],
                store,
            ).prepare_skill_index().require_skill("research", "prompt")

            self.assertEqual(1, entry.call_count)
            self.assertEqual(1, entry.success_count)
            self.assertGreater(entry.freshness, 70)


def _create_core(root: Path) -> ProgressiveDisclosureCore:
    return ProgressiveDisclosureCore(
        [root / "skills"],
        create_local_runtime_store(root / "state"),
    )


def _write_all_skill_kinds(root: Path) -> None:
    _write_prompt_skill(root, "echo", triggers=["echo"])
    _write_mcp_skill(root, "filesystem")
    _write_memory_skill(root, "default")
    _write_workflow_skill(root, "direct")


def _write_prompt_skill(
    root: Path,
    name: str,
    *,
    triggers: list[str] | None = None,
    provides: list[str] | None = None,
    requires: list[str] | None = None,
    instruction: str = "Use this skill.",
) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    _write_manifest(
        skill_dir,
        name,
        "prompt",
        triggers=triggers,
        provides=provides,
        requires=requires,
        include_entry=True,
    )
    (skill_dir / "SKILL.md").write_text(instruction, encoding="utf-8")


def _write_mcp_skill(root: Path, name: str) -> None:
    skill_dir = root / "skills" / "mcp" / name
    skill_dir.mkdir(parents=True)
    _write_manifest(skill_dir, name, "mcp", triggers=[name], include_entry=True, extra="""
[configuration]
transport = "stdio"
command = "example-mcp"
args = []
""")
    (skill_dir / "SKILL.md").write_text("Use MCP tools.", encoding="utf-8")


def _write_memory_skill(root: Path, name: str) -> None:
    skill_dir = root / "skills" / "memory" / name
    skill_dir.mkdir(parents=True)
    _write_manifest(skill_dir, name, "memory", extra="""
[configuration]
default_scope = "agent"
""")


def _write_workflow_skill(root: Path, name: str) -> None:
    skill_dir = root / "skills" / "workflow" / name
    skill_dir.mkdir(parents=True)
    _write_manifest(skill_dir, name, "workflow", extra="""
[configuration]
mode = "direct"
""")


def _write_manifest(
    skill_dir: Path,
    name: str,
    capability: str,
    *,
    triggers: list[str] | None = None,
    provides: list[str] | None = None,
    requires: list[str] | None = None,
    include_entry: bool = False,
    extra: str = "",
) -> None:
    lines = [
        "schema_version = 2",
        f'name = "{name}"',
        f'capability = "{capability}"',
        f'description = "{name} skill"',
        'version = "0.1.0"',
        f"triggers = {_toml_array(triggers or [])}",
        f"provides = {_toml_array(provides or [name])}",
        f"requires = {_toml_array(requires or [])}",
    ]
    if include_entry:
        lines.extend(["", "[entry]", 'instructions = "SKILL.md"'])
    if extra.strip():
        lines.extend(["", extra.strip()])
    (skill_dir / "skill.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(f'"{value}"' for value in values) + "]"
