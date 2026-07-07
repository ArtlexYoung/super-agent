import tempfile
import unittest
from pathlib import Path

from super_agent import Agent, AgentConfig
from super_agent.core.provider import MockProvider


class SubAgentTests(unittest.TestCase):
    def test_add_subagent_uses_clear_name_or_auto_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main = _agent(Path(tmp), "main", "main-ok")
            coder = _agent(Path(tmp), "coder", "coder-ok")
            reviewer = _agent(Path(tmp), "reviewer", "reviewer-ok")

            first_name = main.add_subagent(coder, name="coder", description="writes code")
            second_name = main.add_subagent(reviewer)

            self.assertEqual("coder", first_name)
            self.assertEqual("subagent01", second_name)
            self.assertEqual(["coder", "subagent01"], [item.name for item in main.list_subagents()])

    def test_main_agent_includes_matching_subagent_result_before_final_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main_provider = MockProvider("main-final")
            coder_provider = MockProvider("coder-result")
            main = _agent(root, "main", provider=main_provider)
            coder = _agent(root, "coder", provider=coder_provider)
            main.add_subagent(coder, name="coder", description="writes code", triggers=["code"])

            result = main.run("please write code")

            self.assertEqual("main-final", result.text)
            self.assertEqual(["coder"], [item.name for item in result.subagent_results])
            self.assertEqual("coder-result", result.subagent_results[0].text)
            self.assertIn("Subagent results", main_provider.last_messages[0]["content"])
            self.assertIn("coder-result", main_provider.last_messages[0]["content"])

    def test_main_agent_skips_unmatched_subagents_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main_provider = MockProvider("main-final")
            coder_provider = MockProvider("coder-result")
            main = _agent(root, "main", provider=main_provider)
            coder = _agent(root, "coder", provider=coder_provider)
            main.add_subagent(coder, name="coder", triggers=["code"])

            result = main.run("summarize this")

            self.assertEqual([], result.subagent_results)
            self.assertEqual([], coder_provider.last_messages)

    def test_nested_subagents_can_run_without_depth_safety_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main_provider = MockProvider("main-final")
            coder_provider = MockProvider("coder-result")
            reviewer_provider = MockProvider("reviewer-result")
            main = _agent(root, "main", provider=main_provider)
            coder = _agent(root, "coder", provider=coder_provider)
            reviewer = _agent(root, "reviewer", provider=reviewer_provider)
            main.add_subagent(coder, name="coder")
            coder.add_subagent(reviewer, name="reviewer")

            result = main.run("please write code and review it")

            self.assertEqual("main-final", result.text)
            self.assertEqual("please write code and review it", reviewer_provider.last_messages[-1]["content"])
            self.assertIn("reviewer-result", coder_provider.last_messages[0]["content"])
            self.assertIn("coder-result", main_provider.last_messages[0]["content"])

    def test_agent_warns_when_configured_chain_depth_is_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = _agent(root, "main", max_agent_chain_depth=2)
            coder = _agent(root, "coder")
            reviewer = _agent(root, "reviewer")
            tester = _agent(root, "tester")
            main.add_subagent(coder, name="coder")
            coder.add_subagent(reviewer, name="reviewer")
            reviewer.add_subagent(tester, name="tester")

            warnings = main.check_subagent_links()

            self.assertIn("Agent chain depth is 4 layers, configured max_agent_chain_depth is 2", warnings[0])
            self.assertIn("main -> coder -> reviewer -> tester", warnings[0])

    def test_agent_run_returns_depth_warning_without_stopping_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = _agent(root, "main", "main-ok", max_agent_chain_depth=1)
            coder = _agent(root, "coder", "coder-ok")
            main.add_subagent(coder, name="coder")

            result = main.run("please write code")

            self.assertEqual("main-ok", result.text)
            self.assertEqual("coder-ok", result.subagent_results[0].text)
            self.assertIn("Agent chain depth is 2 layers", result.warning_messages[0])

    def test_agent_warning_uses_subagent_name_in_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = _agent(root, "main", max_agent_chain_depth=1)
            coder = _agent(root, "coder")
            main.add_subagent(coder)

            warnings = main.check_subagent_links()

            self.assertIn("main -> subagent01", warnings[0])

    def test_agent_warns_when_subagent_chain_has_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = _agent(root, "main")
            coder = _agent(root, "coder")
            reviewer = _agent(root, "reviewer")
            main.add_subagent(coder, name="coder")
            coder.add_subagent(reviewer, name="reviewer")
            reviewer.add_subagent(main, name="main")

            warnings = main.check_subagent_links()

            self.assertIn("Agent chain has cycle: main -> coder -> reviewer -> main", warnings)

    def test_subagent_result_keeps_created_flag_and_child_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = _agent(root, "main", "main-ok")
            coder = _agent(root, "coder", "coder-ok")
            helper = _agent(root, "helper", "helper-ok")
            main.add_subagent(coder, name="coder", created_by_agent=True)
            coder.add_subagent(helper, name="helper", created_by_agent=True)

            result = main.run("build this")

            self.assertEqual("build this", result.subagent_results[0].prompt)
            self.assertTrue(result.subagent_results[0].created_by_agent)
            self.assertEqual("helper", result.subagent_results[0].subagent_results[0].name)
            self.assertEqual("build this", result.subagent_results[0].subagent_results[0].prompt)
            self.assertTrue(result.subagent_results[0].subagent_results[0].created_by_agent)


def _agent(
    root: Path,
    name: str,
    response: str = "ok",
    provider: MockProvider | None = None,
    max_agent_chain_depth: int | None = None,
) -> Agent:
    config_path = root / f"{name}.toml"
    memory_path = f".super-agent/memory/{name}"
    max_depth_line = "" if max_agent_chain_depth is None else f"max_agent_chain_depth = {max_agent_chain_depth}"
    config_path.write_text(
        f"""
[agent]
name = "{name}"
system = "{name} system."
workflow = "direct"
skills = []
{max_depth_line}

[model]
provider = "mock"
model = "unit-test"

[paths]
skills = ["skills"]
memory = "{memory_path}"
""".strip(),
        encoding="utf-8",
    )
    return Agent(AgentConfig.load_from_file(config_path), provider=provider or MockProvider(response))
