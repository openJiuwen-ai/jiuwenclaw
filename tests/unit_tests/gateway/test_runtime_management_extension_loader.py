from pathlib import Path


def test_runtime_management_loader_uses_dynamic_extension_package() -> None:
    source = Path(
        "packages/jiuwenclaw-ee/gateway/extensions/"
        "runtime_management_extension/runtime_management_client.py"
    ).read_text(encoding="utf-8")

    assert "jiuwenclaw.gateway.extensions" not in source
    assert 'importlib.import_module(f"{_EXT_PKG}.core.enterprise_config.loader")' in source
    assert 'importlib.import_module(f"{_EXT_PKG}.core.enterprise_config.schemas")' in source
    assert "TemplateRefSlot.EXTENSION_CONFIG" in source
