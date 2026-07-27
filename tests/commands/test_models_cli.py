from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cli import main


class ModelsCliTests(unittest.TestCase):
    def test_save_update_default_and_remove_model_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "agent.toml"
            main(["init", "--path", tmp])

            first = _save_model(config_path, _model_request("fast", default=True))
            second = _save_model(config_path, _model_request("deep", default=True))
            updated_request = _model_request("precise", default=True)
            updated_request["previous_name"] = "deep"
            updated_request["quality_score"] = 0.95
            updated = _save_model(config_path, updated_request)
            listed = _list_models(config_path)

            self.assertEqual("0.1.0", first["model"]["version"])
            self.assertEqual("0.1.0", second["model"]["version"])
            self.assertEqual("0.1.1", updated["model"]["version"])
            self.assertFalse((root / "skills" / "model" / "deep").exists())
            models = {item["name"]: item for item in listed["models"]}
            self.assertEqual({"fast", "precise"}, set(models))
            self.assertFalse(models["fast"]["default"])
            self.assertTrue(models["precise"]["default"])
            self.assertEqual(0.95, models["precise"]["quality_score"])

            removed = StringIO()
            with redirect_stdout(removed):
                code = main(
                    [
                        "models",
                        "remove",
                        "--config",
                        str(config_path),
                        "--name",
                        "precise",
                        "--output",
                        "json",
                    ]
                )
            remaining = _list_models(config_path)["models"]

            self.assertEqual(0, code)
            self.assertTrue(json.loads(removed.getvalue())["removed"])
            self.assertEqual(["fast"], [item["name"] for item in remaining])
            self.assertTrue(remaining[0]["default"])

    def test_save_keeps_environment_name_without_secret_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "agent.toml"
            main(["init", "--path", tmp])
            request = _model_request("remote", default=True)
            request["provider"] = "openai-compatible"
            request["api_key_env"] = "OPENAI_API_KEY"

            saved = _save_model(config_path, request)
            text = (root / "skills" / "model" / "remote" / "skill.toml").read_text(
                encoding="utf-8"
            )

            self.assertEqual("OPENAI_API_KEY", saved["model"]["api_key_env"])
            self.assertNotIn("secret-value", text)


def _save_model(config_path: Path, request: dict[str, object]) -> dict[str, object]:
    output = StringIO()
    with patch("sys.stdin", StringIO(json.dumps(request))), redirect_stdout(output):
        code = main(
            [
                "models",
                "save",
                "--config",
                str(config_path),
                "--request-stdin",
                "--output",
                "json",
            ]
        )
    if code != 0:
        raise AssertionError(f"model save failed: {output.getvalue()}")
    return json.loads(output.getvalue())


def _list_models(config_path: Path) -> dict[str, object]:
    output = StringIO()
    with redirect_stdout(output):
        code = main(
            [
                "models",
                "list",
                "--config",
                str(config_path),
                "--output",
                "json",
            ]
        )
    if code != 0:
        raise AssertionError(f"model list failed: {output.getvalue()}")
    return json.loads(output.getvalue())


def _model_request(name: str, *, default: bool) -> dict[str, object]:
    return {
        "name": name,
        "description": f"{name} model",
        "provider": "mock",
        "model": f"{name}-model",
        "base_url": "",
        "api_key_env": "",
        "supports": ["text"],
        "purposes": ["answer"],
        "strengths": [name],
        "triggers": [name],
        "default": default,
        "agent_can_update": True,
        "agent_can_update_connection": False,
        "quality_score": 0.8,
        "expected_latency_ms": 100,
        "input_cost_per_million": 0.1,
        "output_cost_per_million": 0.2,
    }
