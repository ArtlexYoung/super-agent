import unittest

import super_agent


class PublicApiTests(unittest.TestCase):
    def test_experimental_contract_is_available_from_public_facade(self) -> None:
        expected_names = {
            "Agent",
            "BenchmarkCase",
            "BenchmarkReport",
            "RunEvent",
            "SkillBenchmark",
            "SkillManifest",
            "run_event_from_dict",
            "run_event_to_dict",
            "skill_manifest_to_dict",
        }

        self.assertTrue(expected_names <= set(super_agent.__all__))
        for name in expected_names:
            self.assertIsNotNone(getattr(super_agent, name))
