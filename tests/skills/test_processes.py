import sys
import tempfile
import time
import unittest
from pathlib import Path

from adapter.processes import DeclaredProcessTools, ProcessLimits


class DeclaredProcessTests(unittest.TestCase):
    def test_declared_argv_runs_without_shell_interpretation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools = DeclaredProcessTools(
                Path(tmp),
                [[sys.executable, "-c", "import sys; print(sys.argv[1])", "hello; exit 9"]],
                "allow",
                ProcessLimits(timeout_seconds=3, output_bytes=1_024),
            )

            started = tools.start_process({"command_number": 1})
            result = _wait_for_process(tools, str(started["process_id"]))

            self.assertEqual("completed", result["state"])
            self.assertEqual(0, result["returncode"])
            self.assertTrue(result["passed"])
            self.assertEqual("hello; exit 9\n", result["stdout"])
            self.assertTrue(result["output_complete"])
            self.assertFalse(result["decode_replaced"])

    def test_declared_process_times_out_and_stops_its_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools = DeclaredProcessTools(
                Path(tmp),
                [[sys.executable, "-c", "import time; time.sleep(10)"]],
                "allow",
                ProcessLimits(timeout_seconds=1, output_bytes=1_024),
            )

            started = tools.start_process({"command_number": 1})
            result = _wait_for_process(tools, str(started["process_id"]), timeout=3)

            self.assertEqual("timed_out", result["state"])
            self.assertTrue(result["timed_out"])
            self.assertFalse(result["passed"])
            self.assertIsNotNone(result["returncode"])

    def test_declared_process_marks_output_limit_instead_of_hiding_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools = DeclaredProcessTools(
                Path(tmp),
                [[sys.executable, "-c", "print('x' * 10000)"]],
                "allow",
                ProcessLimits(timeout_seconds=3, output_bytes=32),
            )

            started = tools.start_process({"command_number": 1})
            result = _wait_for_process(tools, str(started["process_id"]))

            self.assertEqual("output_limit_exceeded", result["state"])
            self.assertTrue(result["output_limit_exceeded"])
            self.assertFalse(result["output_complete"])
            self.assertEqual(32, result["output_bytes"])

    def test_declared_process_marks_non_utf8_output_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools = DeclaredProcessTools(
                Path(tmp),
                [[sys.executable, "-c", "import os; os.write(1, b'\\xff')"]],
                "allow",
            )

            started = tools.start_process({"command_number": 1})
            result = _wait_for_process(tools, str(started["process_id"]))

            self.assertEqual("completed", result["state"])
            self.assertTrue(result["decode_replaced"])
            self.assertEqual("\ufffd", result["stdout"])

    def test_declared_process_can_be_stopped_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools = DeclaredProcessTools(
                Path(tmp),
                [[sys.executable, "-c", "import time; time.sleep(10)"]],
                "allow",
            )
            started = tools.start_process({"command_number": 1})

            result = tools.stop_process({"process_id": started["process_id"]})

            self.assertEqual("stopped", result["state"])
            self.assertTrue(result["stopped"])
            self.assertIsNotNone(result["returncode"])

    def test_declared_check_returns_failure_evidence_without_repairing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.txt"
            marker.write_text("unchanged", encoding="utf-8")
            tools = DeclaredProcessTools(
                Path(tmp),
                [[sys.executable, "-c", "import sys; print('failed'); sys.exit(2)"]],
                "allow",
            )

            result = tools.run_check({"command_number": 1})

            self.assertEqual("completed", result["state"])
            self.assertEqual(2, result["returncode"])
            self.assertFalse(result["passed"])
            self.assertIn("failed", result["stdout"])
            self.assertEqual("unchanged", marker.read_text(encoding="utf-8"))

    def test_declared_process_rejects_undeclared_or_denied_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            allowed = DeclaredProcessTools(Path(tmp), [], "allow")
            denied = DeclaredProcessTools(
                Path(tmp),
                [[sys.executable, "-c", "print('no')"]],
                "deny",
            )

            with self.assertRaisesRegex(ValueError, "between 1 and 0"):
                allowed.start_process({"command_number": 1})
            with self.assertRaisesRegex(PermissionError, "denies workspace execute"):
                denied.start_process({"command_number": 1})

            with self.assertRaisesRegex(ValueError, "non-empty string arrays"):
                DeclaredProcessTools(Path(tmp), [[]], "allow")
            with self.assertRaisesRegex(ValueError, "must be allow, ask, or deny"):
                DeclaredProcessTools(Path(tmp), [], "sometimes")
            with self.assertRaisesRegex(ValueError, "between 1 and 300"):
                ProcessLimits(timeout_seconds=True)

    def test_process_tools_have_explicit_start_poll_and_stop_names(self) -> None:
        tools = DeclaredProcessTools(Path.cwd(), [], "ask").list_tools()

        self.assertEqual(
            {
                "start_declared_process",
                "poll_declared_process",
                "stop_declared_process",
                "run_declared_check",
            },
            {tool.name for tool in tools},
        )
        start = next(tool for tool in tools if tool.name == "start_declared_process")
        self.assertEqual(
            "workspace:command:2",
            start.action.resolve_resource({"command_number": 2}),
        )


def _wait_for_process(
    tools: DeclaredProcessTools,
    process_id: str,
    timeout: float = 2,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = tools.poll_process({"process_id": process_id})
        if result["state"] not in {"running", "collecting_output"} and result[
            "returncode"
        ] is not None:
            return result
        time.sleep(0.01)
    tools.stop_process({"process_id": process_id})
    raise AssertionError("declared process did not finish before the test deadline")


if __name__ == "__main__":
    unittest.main()
