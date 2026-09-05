import unittest

from super_agent.core.resources import ResourceCenter


class ResourceCenterTests(unittest.TestCase):
    def test_all_content_operations_use_one_underlying_store(self):
        center = ResourceCenter()

        disclosed = center.disclose_resource("skill:demo", "abcdef", max_characters=2)
        cached = center.read_cached_resource(disclosed.cache_path, max_characters=2)

        self.assertIsInstance(center, ResourceCenter)
        self.assertEqual("ab", cached.content)
        self.assertEqual(2, len(center.read_history()))
        self.assertEqual(
            "read_disclosed_content",
            center.create_read_tool().name,
        )

    def test_resource_center_owns_its_cache_configuration(self):
        with self.assertRaises(ValueError):
            ResourceCenter("cache", max_entries=0)


if __name__ == "__main__":
    unittest.main()
