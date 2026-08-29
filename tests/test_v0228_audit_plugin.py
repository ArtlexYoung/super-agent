import unittest
from pathlib import Path

from adapter.storage import MemoryStorage
from core import __version__
from core.records import AuditPolicy, EventStore
from skill.library import AgentLibrary


ROOT = Path(__file__).resolve().parents[1]
BUILTIN = ROOT / "src" / "skill" / "builtin"


class AuditPluginTests(unittest.TestCase):
    def test_audit_plugin_is_independent_and_auditable(self):
        library = AgentLibrary((BUILTIN,))
        plugin = library.find_plugin("plugin:super-agent/audit")

        self.assertEqual((), plugin.skills)
        self.assertEqual(__version__, plugin.version)

        store = EventStore(MemoryStorage(), "alice", "agent")
        record = store.append(
            "run", "run-1", "run.started", {"prompt": "private", "api_key": "secret"}
        )
        view = AuditPolicy().audit_view([record])

        self.assertNotIn("private", str(view))
        self.assertNotIn("secret", str(view))
        self.assertEqual("private", record.data["prompt"])
        self.assertEqual("critical", view[0]["retention"])

    def test_cleanup_is_previewed_before_deletion(self):
        store = MemoryStorage()
        events = EventStore(store, "alice", "agent")
        events.append("run", "old", "model.usage", {"input_tokens": 1}, created_at="2020-01-01T00:00:00+00:00")
        policy = AuditPolicy(detailed_days=1, critical_days=365)

        preview = policy.prune(store, user_id="alice", apply=False)
        self.assertEqual(1, preview["detailed_candidates"])
        self.assertEqual(1, len(events.read("run", "old")))


if __name__ == "__main__":
    unittest.main()
