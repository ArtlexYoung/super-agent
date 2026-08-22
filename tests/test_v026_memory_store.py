import unittest

from core.records import MemoryStore
from skill.memory import Memory


class ListMemoryStore:
    def __init__(self):
        self.items = []
        self.writes = []

    def read_items(self):
        return list(self.items)

    def append_item(self, memory_id, event_type, data):
        self.writes.append((memory_id, event_type))
        self.items.append(dict(data))


class MemoryStoreTests(unittest.TestCase):
    def test_memory_depends_on_only_the_small_store_contract(self):
        store = ListMemoryStore()
        self.assertTrue(isinstance(store, MemoryStore))
        memory = Memory(store)
        item = memory.remember_long_term("stable user preference")

        self.assertEqual([(item.memory_id, "memory.created")], store.writes)
        self.assertEqual(item, memory.list_items()[0])

    def test_temporary_items_never_call_the_store(self):
        store = ListMemoryStore()
        Memory(store).remember_temporary("current context", conversation_id="conversation-1")
        self.assertEqual([], store.writes)


if __name__ == "__main__":
    unittest.main()
