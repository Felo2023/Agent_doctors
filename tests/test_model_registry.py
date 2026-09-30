from pathlib import Path
import tempfile
import unittest

import yaml

from runtime.models import load_models, resolve_model, opencode_configuration


class ModelRegistryTests(unittest.TestCase):
    def test_current_sol_roles_and_scripts_use_verified_61(self):
        models = load_models()
        self.assertEqual(models["sol"], "openai/gpt-6.1-sol")
        self.assertEqual(models["astra"], "openai/gpt-6-astra")
        from scripts.synthetic_roundtrip import ROLES as participants
        from validation.model_runner import MODELS as evaluation
        self.assertEqual(participants["doctor-b"], models["sol"])
        self.assertEqual(evaluation, models)
        config = opencode_configuration(models["sol"])
        entry = config["provider"]["openai"]["models"]["gpt-6.1-sol"]
        self.assertEqual(entry["id"], "gpt-6.1-sol")
        self.assertTrue(entry["tool_call"])
        self.assertEqual(config["share"], "disabled")
        self.assertNotIn("apiKey", config["provider"]["openai"])

    def test_unresolved_ids_wrong_provider_and_silent_fallback_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.yaml"
            valid = {"schema_version": 1, "silent_fallback": False, "unresolved_model_policy": "fail",
                     "models": {alias: {"provider": "openai", "provider_model_id": "openai/test-model",
                                       "verified_at": "2026-09-30"} for alias in ("opus", "astra", "sol")}}
            path.write_text(yaml.safe_dump(valid))
            self.assertEqual(load_models(path)["sol"], "openai/test-model")
            valid["models"]["sol"]["provider_model_id"] = None
            path.write_text(yaml.safe_dump(valid))
            with self.assertRaisesRegex(ValueError, "unresolved"):
                load_models(path)
            valid["models"]["sol"]["provider_model_id"] = "anthropic/test-model"
            path.write_text(yaml.safe_dump(valid))
            with self.assertRaisesRegex(ValueError, "invalid or unverified"):
                load_models(path)
            valid["silent_fallback"] = True
            path.write_text(yaml.safe_dump(valid))
            with self.assertRaisesRegex(ValueError, "forbid fallback"):
                load_models(path)
        with self.assertRaisesRegex(ValueError, "unknown model alias"):
            resolve_model("nonexistent")
