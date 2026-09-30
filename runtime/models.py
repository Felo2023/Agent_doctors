"""Resolve role model aliases from one explicit registry; never substitute IDs."""

from pathlib import Path
import re

import yaml


REGISTRY = Path(__file__).resolve().parents[1] / "config/models.yaml"


def load_models(path=REGISTRY):
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("schema_version") != 1
            or data.get("silent_fallback") is not False
            or data.get("unresolved_model_policy") != "fail"):
        raise ValueError("model registry must explicitly forbid fallback")
    models = data.get("models")
    if not isinstance(models, dict) or not {"opus", "astra", "sol"}.issubset(models):
        raise ValueError("required model aliases missing")
    resolved = {}
    for alias, model in models.items():
        if not isinstance(model, dict):
            raise ValueError("invalid model configuration")
        identifier = model.get("provider_model_id")
        provider = model.get("provider")
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9_-]+/[a-z0-9_.-]+", identifier)
                or identifier.split("/", 1)[0] != provider or not model.get("verified_at")):
            raise ValueError("model ID unresolved, invalid or unverified for " + str(alias))
        resolved[alias] = identifier
    return resolved


def resolve_model(alias):
    models = load_models()
    if alias not in models:
        raise ValueError("unknown model alias: " + str(alias))
    return models[alias]


def opencode_configuration(model_id):
    """Register a verified new model when isolated OpenCode catalogs lag.

    The shapes used here are documented in https://opencode.ai/config.json.
    This is explicit metadata registration, not an ID substitution or fallback.
    """
    models = load_models()
    alias = next((key for key, value in models.items() if value == model_id), None)
    if alias is None:
        raise ValueError("model ID is not in the verified registry")
    raw = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    definition = raw["models"][alias].get("opencode_definition")
    config = {"$schema": "https://opencode.ai/config.json", "share": "disabled"}
    if definition is not None:
        provider, identifier = model_id.split("/", 1)
        if not isinstance(definition, dict) or definition.get("id") != identifier:
            raise ValueError("explicit OpenCode definition must match verified model ID")
        config["provider"] = {provider: {"models": {identifier: definition}}}
    return config
