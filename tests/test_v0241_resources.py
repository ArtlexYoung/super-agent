import unittest

from core.disclosure import DisclosureStore
from core.resources import ResourceCenter


class ResourceCenterTests(unittest.TestCase):
    def test_all_content_operations_use_one_underlying_store(self):
        center = ResourceCenter()

        disclosed = center.disclose_resource("skill:demo", "abcdef", max_characters=2)
        cached = center.read_cached_resource(disclosed.cache_path, max_characters=2)

        self.assertIsInstance(center.store, DisclosureStore)
        self.assertEqual("ab", cached.content)
        self.assertEqual(2, len(center.read_history()))
        self.assertEqual(
            "read_disclosed_content",
            center.create_read_tool().name,
        )

    def test_a_supplied_store_is_not_reconfigured_by_the_center(self):
        store = DisclosureStore()
        center = ResourceCenter(store)

        self.assertIs(store, center.store)
        with self.assertRaises(ValueError):
            ResourceCenter(store, cache_root="cache")


if __name__ == "__main__":
    unittest.main()
