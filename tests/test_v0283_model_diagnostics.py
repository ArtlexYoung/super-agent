"""统一模型配置诊断：check 提供逐模型可行动信息，不泄露密钥。"""

import os
import unittest
from unittest import mock

import adapter.cli as cli
from core.config import config_from_environment


class ModelDiagnosticsTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("OPENAI_API_KEY", None)

    def _config(self, environment):
        with mock.patch.dict(os.environ, environment, clear=False):
            return config_from_environment(environment)

    def test_no_model_reports_actionable_step(self):
        diagnostics = cli._model_diagnostics(config_from_environment({}))
        self.assertEqual(1, len(diagnostics))
        self.assertFalse(diagnostics[0]["ready"])
        self.assertIn("SUPER_AGENT_PROVIDER", diagnostics[0]["action"])

    def test_mock_needs_no_credentials(self):
        diagnostics = cli._model_diagnostics(
            self._config({"SUPER_AGENT_PROVIDER": "mock", "SUPER_AGENT_MODEL": "Mock"})
        )
        self.assertTrue(diagnostics[0]["ready"])
        self.assertIsNone(diagnostics[0]["api_key_env"])
        self.assertIn("Mock", diagnostics[0]["action"])

    def test_missing_key_flags_ready_false(self):
        os.environ.pop("OPENAI_API_KEY", None)
        diagnostics = cli._model_diagnostics(
            self._config(
                {
                    "SUPER_AGENT_PROVIDER": "openai",
                    "SUPER_AGENT_MODEL": "gpt-4o",
                    "SUPER_AGENT_BASE_URL": "https://api.openai.com/v1",
                }
            )
        )
        self.assertFalse(diagnostics[0]["ready"])
        self.assertFalse(diagnostics[0]["api_key_present"])
        self.assertEqual("OPENAI_API_KEY", diagnostics[0]["api_key_env"])
        self.assertIn("OPENAI_API_KEY", diagnostics[0]["action"])

    def test_present_key_does_not_leak_secret(self):
        os.environ["OPENAI_API_KEY"] = "sk-test-secret-value"
        diagnostics = cli._model_diagnostics(
            self._config({"SUPER_AGENT_PROVIDER": "openai", "SUPER_AGENT_MODEL": "gpt-4o"})
        )
        self.assertTrue(diagnostics[0]["ready"])
        rendered = repr(diagnostics)
        self.assertNotIn("sk-test-secret-value", rendered)
        self.assertIn("base_url", diagnostics[0]["action"])


if __name__ == "__main__":
    unittest.main()
