import shutil
import tempfile
import unittest
from pathlib import Path

from core.config import EvolutionConfig, config_from_dict
from core.disclosure import DisclosureStore
from skill.document import format_skill
from skill.evolution import SkillEvolution, SkillTestCase
from skill.library import PluginCatalog


def write_plugin(
    root: Path,
    plugin_id: str,
    body: str,
    *,
    version: str = "1.0.0",
    members: dict[str, tuple[str, ...]] | None = None,
) -> Path:
    root.mkdir(parents=True)
    name = plugin_id.rsplit("/", 1)[-1]
    (root / "plugin.toml").write_text(
        f'schema = 1\nid = "{plugin_id}"\nversion = "{version}"\nrequires = []\n',
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text(
        format_skill(
            {"name": name, "description": f"{name} plugin", "version": version},
            body,
        ),
        encoding="utf-8",
    )
    for member, includes in (members or {}).items():
        path = root / "skills" / member / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            format_skill(
                {
                    "name": member,
                    "description": f"{member} method",
                    "version": version,
                    "includes": includes,
                },
                body,
            ),
            encoding="utf-8",
        )
    return root


class PluginCatalogTests(unittest.TestCase):
    def test_builtin_plugins_have_one_owner_for_each_skill(self):
        root = Path(__file__).resolve().parents[1] / "src" / "skill" / "builtin"
        snapshot = PluginCatalog((root,)).snapshot()

        self.assertEqual(
            {"super-agent/common", "super-agent/code"}, set(snapshot.plugins)
        )
        self.assertEqual(
            ("super-agent/common",), snapshot.plugins["super-agent/code"].requires
        )
        self.assertEqual(len(snapshot.skills), len(set(snapshot.skills)))
        self.assertIn("skill:super-agent/common/multi-agent", snapshot.skills)
        self.assertNotIn("skill:super-agent/code/multi-agent", snapshot.skills)

    def test_identical_plugin_is_reused_across_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = write_plugin(base / "first" / "demo", "example/demo", "same")
            second = base / "second" / "demo"
            second.parent.mkdir()
            shutil.copytree(first, second)

            plugin = PluginCatalog((first.parent, second.parent)).find_plugin(
                "plugin:example/demo"
            )

            self.assertEqual(2, len(plugin.sources))

    def test_same_identity_with_different_content_is_always_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = write_plugin(base / "first" / "demo", "example/demo", "first")
            second = write_plugin(base / "second" / "demo", "example/demo", "second")

            for roots in ((first.parent, second.parent), (second.parent, first.parent)):
                with self.subTest(roots=roots), self.assertRaisesRegex(
                    ValueError, "plugin identity conflicts"
                ):
                    PluginCatalog(roots).snapshot()

    def test_scope_override_uses_base_hash_and_next_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            shared = write_plugin(base / "shared" / "demo", "example/demo", "old")
            catalog = PluginCatalog((shared.parent,), writable_root=base / "owned")
            original = catalog.find_skill("skill:example/demo/main")

            updated = catalog.update_skill(
                original.reference, "new", expected_sha256=original.sha256
            )

            self.assertEqual("old", catalog.find_skill(original.reference).body)
            catalog.refresh()
            self.assertEqual("new", catalog.find_skill(original.reference).body)
            self.assertNotEqual(original.sha256, updated.sha256)
            self.assertEqual(
                "old",
                PluginCatalog((shared.parent,)).find_skill(original.reference).body,
            )

    def test_disclosure_reuses_content_but_keeps_logical_history(self):
        store = DisclosureStore(max_entries=4)
        first = store.disclose("skill:one/main", "same content")
        second = store.disclose("skill:two/main", "same content")

        self.assertEqual(first.cache_path, second.cache_path)
        self.assertEqual(
            ["skill:one/main", "skill:two/main"],
            [item["reference"] for item in store.history()],
        )

    def test_evolution_allow_and_auto_apply_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = PluginCatalog((), writable_root=Path(directory) / "owned")
            catalog.create_plugin("local/demo", "old", description="demo")
            catalog.refresh()
            skill = catalog.find_skill("skill:local/demo/main")
            policy = EvolutionConfig((skill.reference,), ())
            evolution = SkillEvolution(
                catalog, policy=policy, runner=lambda body, _prompt: body
            )
            change = evolution.propose(skill.reference, "new", reason="measured")
            evolution.test(
                change.change_id,
                [SkillTestCase("new", "run", required_text=("new",))],
            )

            with self.assertRaisesRegex(PermissionError, "auto-apply"):
                evolution.apply(change.change_id)

    def test_configuration_rejects_implicit_or_broad_evolution(self):
        config = config_from_dict(
            {
                "plugin_paths": ["plugins"],
                "enabled_plugins": ["plugin:example/demo"],
                "evolution": {
                    "allow": ["skill:example/demo/main"],
                    "auto_apply": [],
                },
            }
        )
        self.assertEqual(("plugins",), config.plugin_paths)
        self.assertEqual(
            ("skill:example/demo/main",), config.evolution.allow
        )
        with self.assertRaisesRegex(ValueError, "subset"):
            EvolutionConfig((), ("plugin:example/demo",))


if __name__ == "__main__":
    unittest.main()
