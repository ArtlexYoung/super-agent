import os
import tempfile
import unittest
from unittest.mock import patch

from tests.storage.verification_support import (
    storage_isolation_report_to_dict,
    verify_multiuser_isolation_across_storage_backends,
)


class StorageIsolationVerificationTests(unittest.TestCase):
    def test_jsonl_and_sqlite_pass_the_same_multiuser_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = verify_multiuser_isolation_across_storage_backends(
                tmp,
                backend_names=["jsonl", "sqlite"],
            )

        data = storage_isolation_report_to_dict(report)
        self.assertEqual(2, data["schema_version"])
        self.assertTrue(data["all_available_backends_passed"])
        self.assertTrue(data["all_backends_verified"])
        self.assertEqual(["passed", "passed"], [item["status"] for item in data["backends"]])
        for item in data["backends"]:
            self.assertIn("temporary_state_cleanup", item["checks"])
            self.assertIn("skill_evolution_user_isolation", item["checks"])
            self.assertIn("skill_disclosure_user_isolation", item["checks"])

    def test_unconfigured_remote_backends_are_reported_as_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            report = verify_multiuser_isolation_across_storage_backends(
                tmp,
                backend_names=["mysql", "postgresql"],
            )

        self.assertTrue(report.all_available_backends_passed)
        self.assertFalse(report.all_backends_verified)
        self.assertEqual(
            ["unavailable", "unavailable"],
            [result.status for result in report.results],
        )

    def test_unknown_or_duplicate_backend_names_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "at least one backend"):
                verify_multiuser_isolation_across_storage_backends(
                    tmp,
                    backend_names=[],
                )
            with self.assertRaisesRegex(ValueError, "must be unique"):
                verify_multiuser_isolation_across_storage_backends(
                    tmp,
                    backend_names=["jsonl", "jsonl"],
                )
            with self.assertRaisesRegex(ValueError, "unknown storage verification backend"):
                verify_multiuser_isolation_across_storage_backends(
                    tmp,
                    backend_names=["unknown"],
                )
