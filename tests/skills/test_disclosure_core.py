import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from skill.evolution.records import (
    EvaluationResult,
    EvaluationSource,
    EvaluationTokenUsage,
    append_evaluation_records,
    create_evaluation_record,
    read_evaluation_records,
)
from skill.state.events import create_local_event_store
from skill.disclosure import ProgressiveDisclosureCore
from skill.evolution.freshness import calculate_skill_freshness
from skill.evolution.values import SkillRevision
from skill.loaders.defaults import create_runtime_disclosure_recorder
from skill.skills import Skills
from skill.task.run import Run
from support import load_default_evolution_policy


class ProgressiveDisclosureCoreTests(unittest.TestCase):
    def test_run_owns_one_central_skills_snapshot(self) -> None:
        skills = Skills(ProgressiveDisclosureCore([]))
        run_fields = {field.name for field in fields(Run)}

        self.assertIs(skills.index, skills.disclosure.require_prepared_skill_index())
        self.assertIn("skills", run_fields)
        self.assertFalse(
            {"skill_disclosure", "skill_index", "skill_loaders"} & run_fields
        )

    def test_read_only_index_contains_every_skill_type_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_all_skill_kinds(root)

            core = _create_core(root)
            index = core.prepare_skill_index()

            self.assertEqual(
                ["mcp:filesystem", "memory:default", "prompt:echo", "workflow:direct"],
                [entry.reference.key for entry in index.entries],
            )
            self.assertIsNone(index.index_path)
            self.assertIsNone(index.history_path)
            self.assertFalse((root / "state").exists())
            prompt = index.build_progressive_disclosure_prompt()
            self.assertIn("prompt:echo", prompt)
            self.assertNotIn("cache", prompt)

    def test_same_name_in_different_kinds_requires_explicit_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "default")
            _write_memory_skill(root, "default")
            core = _create_core(root)
            core.prepare_skill_index()

            with self.assertRaisesRegex(ValueError, "ambiguous Skill name default"):
                core.open_skill("default")

            manifest = core.open_skill("default", expected_type="memory").read_manifest()
            self.assertEqual("memory", manifest.skill_type)

    def test_project_skill_source_overrides_builtin_with_the_same_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary = root / "primary" / "planner" / "default"
            builtin = root / "builtin" / "planner" / "default"
            primary.mkdir(parents=True)
            builtin.mkdir(parents=True)
            _write_manifest(primary, "default", "planner")
            _write_manifest(builtin, "default", "planner")
            core = ProgressiveDisclosureCore(
                [root / "primary"],
                builtin_skill_roots=[root / "builtin"],
            )

            index = core.prepare_skill_index()
            manifest = core.open_skill("default", "planner").read_manifest()

            self.assertEqual(["planner:default"], [entry.reference.key for entry in index.entries])
            self.assertEqual(primary, manifest.path)
            self.assertEqual("project", index.entries[0].source)

    def test_user_skill_overrides_project_and_is_isolated_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "skills" / "writer"
            project.mkdir(parents=True)
            _write_manifest(project, "writer", "prompt", include_entry=True)
            project.joinpath("SKILL.md").write_text("Project instructions.", encoding="utf-8")
            alice_store = create_local_event_store(root / "state", user_id="alice")
            bob_store = create_local_event_store(root / "state", user_id="bob")
            alice_skill = alice_store.private_root / "skills" / "prompt" / "writer"
            alice_skill.mkdir(parents=True)
            _write_manifest(alice_skill, "writer", "prompt", include_entry=True)
            alice_skill.joinpath("SKILL.md").write_text("Alice instructions.", encoding="utf-8")

            alice = ProgressiveDisclosureCore(
                [root / "skills"],
                user_skill_roots=[alice_store.private_root / "skills"],
            )
            bob = ProgressiveDisclosureCore(
                [root / "skills"],
                user_skill_roots=[bob_store.private_root / "skills"],
            )
            alice_entry = alice.prepare_skill_index().require_skill("writer", "prompt")
            bob_entry = bob.prepare_skill_index().require_skill("writer", "prompt")

            self.assertEqual("user", alice_entry.source)
            self.assertEqual("project", bob_entry.source)
            self.assertEqual(
                "Alice instructions.",
                alice.open_skill("writer", "prompt").read_instructions().content,
            )
            self.assertEqual(
                "Project instructions.",
                bob.open_skill("writer", "prompt").read_instructions().content,
            )
            self.assertEqual("Project instructions.", project.joinpath("SKILL.md").read_text())

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
                provides=["facts"],
                requires=["http"],
            )
            core = _create_core(root)
            core.prepare_skill_index()

            selected = core.select_skill_references(
                selected_names=["research"],
                allowed_types={"prompt", "mcp"},
            )

            self.assertEqual(["prompt:http", "prompt:research"], [item.key for item in selected])

    def test_selection_ignores_configured_skill_disabled_by_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mcp_skill(root, "filesystem")
            core = ProgressiveDisclosureCore(
                [root / "skills"],
                disabled_names=["mcp"],
            )
            core.prepare_skill_index()

            selected = core.select_skill_references(
                selected_names=["filesystem"],
                allowed_types={"prompt", "mcp"},
            )

            self.assertEqual([], selected)

    def test_bare_name_still_selects_enabled_kind_when_other_kind_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "shared")
            _write_mcp_skill(root, "shared")
            core = ProgressiveDisclosureCore(
                [root / "skills"],
                disabled_names=["mcp:shared"],
            )
            core.prepare_skill_index()

            selected = core.select_skill_references(
                selected_names=["shared"],
                allowed_types={"prompt", "mcp"},
            )

            self.assertEqual(["prompt:shared"], [item.key for item in selected])

    def test_disclosure_stages_share_cache_and_record_cache_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "echo", instruction="Answer briefly.")
            core = _create_recording_core(root)
            core.prepare_skill_index()
            skill = core.open_skill("echo", expected_type="prompt")

            manifest = skill.disclose_manifest()
            first = skill.disclose_instructions()
            second = skill.disclose_instructions()
            configuration = skill.disclose_configuration()
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

    def test_read_stages_do_not_write_cache_or_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "echo", instruction="Answer briefly.")
            core = _create_recording_core(root)
            index = core.prepare_skill_index()
            skill = core.open_skill("echo", expected_type="prompt")

            self.assertEqual("echo", skill.read_manifest().name)
            self.assertEqual("Answer briefly.", skill.read_instructions().content)
            self.assertEqual({}, skill.read_configuration().content)
            self.assertTrue(skill.read_skill_files().files)

            entry = index.entries[0]
            self.assertFalse(entry.manifest_cache_path.exists())
            self.assertFalse(entry.instructions_cache_path.exists())
            self.assertFalse(entry.configuration_cache_path.exists())
            self.assertFalse(entry.files_cache_path.exists())
            self.assertEqual(
                ["index"],
                [event.stage for event in core.read_disclosure_history()],
            )

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
            core = _create_recording_core(root)
            index = core.prepare_skill_index()

            disclosed = core.open_skill(
                "echo",
                "prompt",
            ).disclose_skill_files()

            files = {item.relative_path: item for item in disclosed.files}
            self.assertEqual("example content", files["resources/example.txt"].content)
            self.assertIsNone(files["resources/image.bin"].content)
            self.assertEqual(index.entries[0].files_cache_path, disclosed.cache_path)
            self.assertEqual("files", core.read_disclosure_history()[-1].stage)

    def test_read_disclosed_content_rejects_path_outside_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core = _create_recording_core(root)
            core.prepare_skill_index()
            outside = root / "outside.txt"
            outside.write_text("outside", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "outside disclosure cache"):
                core.read_disclosed_content(outside)

    def test_cache_and_history_reads_require_explicit_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            core = _create_core(Path(tmp))
            core.prepare_skill_index()

            with self.assertRaisesRegex(RuntimeError, "recording is not configured"):
                core.read_disclosed_content("missing")
            with self.assertRaisesRegex(RuntimeError, "recording is not configured"):
                core.read_disclosure_history()

    def test_disclosure_write_rejects_path_outside_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = create_local_event_store(root / "state")
            outside = root / "outside.txt"

            with self.assertRaisesRegex(ValueError, "outside disclosure cache"):
                store.disclosure.write_text(
                    None,
                    "prompt:outside",
                    "instructions",
                    outside,
                    "must not be written",
                )

            self.assertFalse(outside.exists())

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

    def test_configuration_is_optional_for_every_skill_type(self) -> None:
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
        from skill.loaders.mcp import read_mcp_skill_settings
        from skill.state.memory_service import create_memory_from_skill_disclosure
        from skill.loaders.workflow import create_workflow_policy_from_skill

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_all_skill_kinds(root)
            core = _create_core(root)
            core.prepare_skill_index()

            mcp_settings = read_mcp_skill_settings(
                core.open_skill("filesystem", expected_type="mcp")
            )
            memory = create_memory_from_skill_disclosure(
                core.open_skill("default", expected_type="memory"),
                create_local_event_store(root / "state"),
            )
            workflow = create_workflow_policy_from_skill(
                core.open_skill("direct", expected_type="workflow")
            )

            self.assertEqual("example-mcp", mcp_settings.server_name)
            self.assertEqual("agent", memory.policy.default_scope)
            self.assertEqual("direct", workflow.mode)

    def test_index_centrally_merges_runtime_freshness_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prompt_skill(root, "research")
            store = create_local_event_store(root / "state")
            append_evaluation_records(
                store,
                [
                    create_evaluation_record(
                        revision=SkillRevision(
                            key="prompt:research",
                            skill_type="prompt",
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
                freshness_stats=calculate_skill_freshness(
                    read_evaluation_records(store, source_type="agent_run"),
                    load_default_evolution_policy(root),
                ),
            ).prepare_skill_index().require_skill("research", "prompt")

            self.assertEqual(1, entry.call_count)
            self.assertEqual(1, entry.success_count)
            self.assertGreater(entry.freshness, 70)


def _create_core(root: Path) -> ProgressiveDisclosureCore:
    return ProgressiveDisclosureCore([root / "skills"])


def _create_recording_core(root: Path) -> ProgressiveDisclosureCore:
    store = create_local_event_store(root / "state")
    return ProgressiveDisclosureCore(
        [root / "skills"],
        recorder=create_runtime_disclosure_recorder(store),
    )


def _write_all_skill_kinds(root: Path) -> None:
    _write_prompt_skill(root, "echo")
    _write_mcp_skill(root, "filesystem")
    _write_memory_skill(root, "default")
    _write_workflow_skill(root, "direct")


def _write_prompt_skill(
    root: Path,
    name: str,
    *,
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
        provides=provides,
        requires=requires,
        include_entry=True,
    )
    (skill_dir / "SKILL.md").write_text(instruction, encoding="utf-8")


def _write_mcp_skill(root: Path, name: str) -> None:
    skill_dir = root / "skills" / "mcp" / name
    skill_dir.mkdir(parents=True)
    _write_manifest(skill_dir, name, "mcp", include_entry=True, extra="""
[configuration]
server = "example-mcp"
""")
    (skill_dir / "SKILL.md").write_text("Use MCP tools.", encoding="utf-8")


def _write_memory_skill(root: Path, name: str) -> None:
    skill_dir = root / "skills" / "memory" / name
    skill_dir.mkdir(parents=True)
    _write_manifest(skill_dir, name, "memory", include_entry=True, extra="""
[configuration]
default_scope = "agent"
organization_candidate_limit = 20
""")
    (skill_dir / "SKILL.md").write_text(
        "Organize memory into concise, durable knowledge.",
        encoding="utf-8",
    )


def _write_workflow_skill(root: Path, name: str) -> None:
    skill_dir = root / "skills" / "workflow" / name
    skill_dir.mkdir(parents=True)
    _write_manifest(skill_dir, name, "workflow", include_entry=True, extra="""
[configuration]
mode = "direct"
max_steps = 8
""")
    (skill_dir / "SKILL.md").write_text(
        "Complete the task directly and return the result.",
        encoding="utf-8",
    )


def _write_manifest(
    skill_dir: Path,
    name: str,
    skill_type: str,
    *,
    provides: list[str] | None = None,
    requires: list[str] | None = None,
    include_entry: bool = False,
    extra: str = "",
) -> None:
    lines = [
        "schema_version = 3",
        f'name = "{name}"',
        f'type = "{skill_type}"',
        f'description = "{name} skill"',
        'version = "0.1.0"',
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
