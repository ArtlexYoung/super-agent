import unittest

from core.resources import ResourceCenter
from skill.library import AgentLibrary


class CentralResourceCenterTests(unittest.TestCase):
    def test_library_index_is_built_by_the_library(self):
        library = AgentLibrary()

        index = library.resource_index(page_size=1)

        self.assertEqual({"plugins", "skills", "mcp_servers"}, set(index))
        self.assertEqual(0, index["plugins"]["total"])

    def test_library_uses_the_explicit_resource_center(self):
        library = AgentLibrary()
        center = ResourceCenter()

        library.use_resource_center(center)

        self.assertIs(center, library.resources)
        self.assertEqual({}, library.snapshot().to_dict()["skills"])
        with self.assertRaises(TypeError):
            library.use_resource_center(object())


if __name__ == "__main__":
    unittest.main()
