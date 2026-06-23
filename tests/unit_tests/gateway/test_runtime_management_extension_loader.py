from pathlib import Path


def test_runtime_management_loader_uses_infrastructure_extension_import() -> None:
    source = Path(
        "packages/jiuwenclaw-ee/gateway/extensions/"
        "runtime_management_extension/runtime_management_client.py"
    ).read_text(encoding="utf-8")

    assert "jiuwenclaw.gateway.channel_config_db" not in source
    assert "jiuwenclaw.infrastructure.module_importer" in source
    assert "import_manager_ws_client_module" in source
    assert 'import_manager_ws_client_module("core.enterprise_config.loader")' in source
    assert 'import_manager_ws_client_module("core.enterprise_config.schemas")' in source
    assert 'import_manager_ws_client_module("core.enterprise_config.gateway_db")' in source
    assert "TemplateRefSlot.EXTENSION_CONFIG" in source
