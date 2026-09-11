"""验证任务检查所用进程输出的完整性与资源回收，不把截断或未排空的输出当成完整证据。"""

import sys
import tempfile
import threading
import unittest
from pathlib import Path

from super_agent.adapter.process import ProcessSettings, ProcessTools


class ProcessEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def tool(self, *commands: tuple[str, ...]) -> ProcessTools:
        tools = ProcessTools(ProcessSettings(self.root, commands))
        self.addCleanup(tools.close)
        return tools

    def run_check(self, tools: ProcessTools, command: tuple[str, ...]) -> dict:
        handler = next(tool for tool in tools.tools() if tool.name == "run_check").handler
        return handler({"command": list(command)}, object())

    def test_synchronous_check_returns_complete_output_and_closes_pipes(self) -> None:
        """进程退出后必须排空读取线程并关闭管道，否则输出与文件描述符都会泄漏。"""
        command = (sys.executable, "-I", "-B", "-c",
                   "import sys; sys.stdout.write('x' * 300000); sys.stderr.write('tail-marker')")
        tools = self.tool(command)
        before = threading.active_count()
        result = self.run_check(tools, command)
        self.assertEqual(0, result["returncode"])
        self.assertTrue(result["output_complete"])
        self.assertFalse(result["output_truncated"])
        self.assertEqual(300000, len(result["stdout"]))
        self.assertEqual("tail-marker", result["stderr"])
        # 读取线程随工具调用结束而退出，不留下常驻线程。
        for _ in range(100):
            if threading.active_count() <= before:
                break
            threading.Event().wait(0.05)
        self.assertLessEqual(threading.active_count(), before)

    def test_truncated_output_is_reported_as_incomplete_evidence(self) -> None:
        """超过输出上限时明确标记截断，不能把截断结果当作完整检查证据。"""
        command = (sys.executable, "-I", "-B", "-c", "print('y' * 5000)")
        tools = ProcessTools(ProcessSettings(self.root, (command,), max_output_bytes=128))
        self.addCleanup(tools.close)
        result = self.run_check(tools, command)
        self.assertTrue(result["output_truncated"])
        self.assertLessEqual(len(result["stdout"]) + len(result["stderr"]), 128)

    def test_close_is_idempotent_and_stops_running_processes(self) -> None:
        """重复关闭不报错；关闭后不再保留进程记录，也不留下运行中的子进程。"""
        command = (sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)")
        tools = ProcessTools(ProcessSettings(self.root, (command,)))
        start = next(tool for tool in tools.tools() if tool.name == "start_process").handler
        started = start({"command": list(command)}, object())
        self.assertEqual("running", started["state"])
        tools.close()
        tools.close()
        self.assertEqual({}, tools._processes)

    def test_process_retention_is_bounded(self) -> None:
        """长期运行的实例不能无界增长：已结束进程的记录数量保持在保留上限内。"""
        command = (sys.executable, "-I", "-B", "-c", "pass")
        tools = self.tool(command)
        handlers = {tool.name: tool.handler for tool in tools.tools()}
        for _ in range(70):
            started = handlers["start_process"]({"command": list(command)}, object())
            for _ in range(200):
                polled = handlers["poll_process"]({"process_id": started["process_id"]}, object())
                if polled["state"] != "running" and polled["output_complete"]:
                    break
                threading.Event().wait(0.02)
            self.assertNotEqual("running", polled["state"])
        self.assertLessEqual(len(tools._processes), 65)
        self.assertGreater(len(tools._processes), 0)


if __name__ == "__main__":
    unittest.main()
