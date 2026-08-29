import unittest
from types import SimpleNamespace

from core.disclosure import DisclosureStore
from skill.agent_builder import RunResourceCenter
from skill.library import AgentLibrary


class CentralResourceCenterTests(unittest.TestCase):
    def test_library_index_is_built_by_the_library(self):
        library = AgentLibrary()

        index = library.resource_index(page_size=1)

        self.assertEqual({"plugins", "skills", "mcp_servers"}, set(index))
        self.assertEqual(0, index["plugins"]["total"])

    def test_tree_and_library_share_one_disclosure_store(self):
        library = AgentLibrary()
        tree_store = DisclosureStore()
        tree = SimpleNamespace(disclosures=tree_store)

        center = RunResourceCenter.create(library, tree)

        self.assertIs(tree_store, center.disclosure_store)
        self.assertIs(tree_store, library.disclosures)
        self.assertEqual({}, center.snapshot()["skills"])


if __name__ == "__main__":
    unittest.main()
