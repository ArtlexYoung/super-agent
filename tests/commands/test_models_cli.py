from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from adapter.cli_adapter.commands import main
from support import write_minimal_project


class ModelsCliTests(unittest.TestCase):
    def test_save_update_default_and_remove_model_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "common.toml"
            write_minimal_project(tmp)

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
            self.assertFalse(_find_user_model_skill(root, "deep").exists())
            models = {item["name"]: item for item in listed["models"]}
            self.assertEqual({"fast", "precise"}, set(models))
            self.assertFalse(models["fast"]["default"])
            self.assertTrue(models["precise"]["default"])
            self.assertEqual(0.95, models["precise"]["quality_score"])
            self.assertEqual(0.3, models["precise"]["cache_creation_cost_per_million"])

            removed = StringIO()
            with redirect_stdout(removed):
                code = main(
                    [
                        "skills",
                        "models",
                        "remove",
                        "--common-config",
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
            config_path = root / "common.toml"
            write_minimal_project(tmp)
            request = _model_request("remote", default=True)
            request["provider"] = "openai-compatible"
            request["api_key_env"] = "OPENAI_API_KEY"

            saved = _save_model(config_path, request)
            text = _find_user_model_skill(root, "remote").joinpath("skill.toml").read_text(
                encoding="utf-8"
            )

            self.assertEqual("OPENAI_API_KEY", saved["model"]["api_key_env"])
            self.assertNotIn("secret-value", text)

    def test_model_commands_read_only_the_selected_user_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "common.toml"
            write_minimal_project(tmp)

            _save_model(
                config_path,
                _model_request("alice-only", default=True),
                user_id="alice",
            )
            alice_names = {
                item["name"]
                for item in _list_models(config_path, user_id="alice")["models"]
            }
            bob_names = {
                item["name"]
                for item in _list_models(config_path, user_id="bob")["models"]
            }

            self.assertIn("alice-only", alice_names)
            self.assertNotIn("alice-only", bob_names)


def _save_model(
    config_path: Path,
    request: dict[str, object],
    *,
    user_id: str = "local",
) -> dict[str, object]:
    output = StringIO()
    with patch("sys.stdin", StringIO(json.dumps(request))), redirect_stdout(output):
        code = main(
            [
                "skills",
                "models",
                "save",
                "--common-config",
                str(config_path),
                "--user-id",
                user_id,
                "--request-stdin",
                "--output",
                "json",
            ]
        )
    if code != 0:
        raise AssertionError(f"model save failed: {output.getvalue()}")
    return json.loads(output.getvalue())


def _list_models(
    config_path: Path,
    *,
    user_id: str = "local",
) -> dict[str, object]:
    output = StringIO()
    with redirect_stdout(output):
        code = main(
            [
                "skills",
                "models",
                "list",
                "--common-config",
                str(config_path),
                "--user-id",
                user_id,
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
        "default": default,
        "agent_can_update": True,
        "agent_can_update_connection": False,
        "quality_score": 0.8,
        "expected_latency_ms": 100,
        "input_cost_per_million": 0.1,
        "output_cost_per_million": 0.2,
        "cache_creation_cost_per_million": 0.3,
        "cache_read_cost_per_million": 0.4,
    }


def _find_user_model_skill(root: Path, name: str) -> Path:
    matches = [
        path.parent
        for path in root.joinpath(".super-agent").rglob(
            f"skills/model/{name}/skill.toml"
        )
        if "cache" not in path.parts
    ]
    if len(matches) > 1:
        raise AssertionError(f"multiple user model Skills found: {name}")
    return matches[0] if matches else root / ".missing-user-model-skill" / name
