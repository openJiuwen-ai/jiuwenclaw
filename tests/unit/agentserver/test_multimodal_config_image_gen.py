from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_MM_PATH = (
    Path(__file__).resolve().parents[3]
    / "jiuwenclaw"
    / "agentserver"
    / "tools"
    / "multimodal_config.py"
)
_spec = importlib.util.spec_from_file_location("multimodal_config_under_test", _MM_PATH)
assert _spec and _spec.loader
_multimodal_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_multimodal_config)
apply_image_gen_model_config_from_yaml = (
    _multimodal_config.apply_image_gen_model_config_from_yaml
)
dedicated_multimodal_model_configured = (
    _multimodal_config.dedicated_multimodal_model_configured
)


@pytest.fixture(autouse=True)
def _clear_image_gen_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "IMAGE_GEN_API_KEY",
        "IMAGE_GEN_API_BASE",
        "IMAGE_GEN_MODEL_NAME",
        "IMAGE_GEN_PROVIDER",
        "API_KEY",
        "API_BASE",
        "MODEL_NAME",
        "MODEL_PROVIDER",
    ):
        monkeypatch.delenv(key, raising=False)


def test_apply_image_gen_model_config_from_yaml_writes_env() -> None:
    config = {
        "models": {
            "image_gen": {
                "model_client_config": {
                    "api_key": "img-key",
                    "api_base": "https://img.example/v1",
                    "model_name": "wanx-v1",
                    "client_provider": "DashScope",
                }
            }
        }
    }
    apply_image_gen_model_config_from_yaml(config)
    assert os.environ["IMAGE_GEN_API_KEY"] == "img-key"
    assert os.environ["IMAGE_GEN_API_BASE"] == "https://img.example/v1"
    assert os.environ["IMAGE_GEN_MODEL_NAME"] == "wanx-v1"
    assert os.environ["IMAGE_GEN_PROVIDER"] == "DashScope"


def test_apply_image_gen_falls_back_to_main_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "main-key")
    monkeypatch.setenv("API_BASE", "https://main.example/v1")
    monkeypatch.setenv("MODEL_NAME", "main-model")
    monkeypatch.setenv("MODEL_PROVIDER", "OpenAI")
    config = {"models": {"image_gen": {"model_client_config": {}}}}
    apply_image_gen_model_config_from_yaml(config)
    assert os.environ["IMAGE_GEN_API_KEY"] == "main-key"
    assert os.environ["IMAGE_GEN_API_BASE"] == "https://main.example/v1"
    assert os.environ["IMAGE_GEN_MODEL_NAME"] == "main-model"
    assert os.environ["IMAGE_GEN_PROVIDER"] == "OpenAI"


def test_dedicated_multimodal_model_configured_image_gen() -> None:
    with_key = {
        "models": {
            "image_gen": {
                "model_client_config": {"api_key": "dedicated"},
            }
        }
    }
    without_key = {
        "models": {
            "image_gen": {
                "model_client_config": {"api_key": ""},
            }
        }
    }
    assert dedicated_multimodal_model_configured(with_key, "image_gen") is True
    assert dedicated_multimodal_model_configured(without_key, "image_gen") is False
    assert dedicated_multimodal_model_configured(None, "image_gen") is False
