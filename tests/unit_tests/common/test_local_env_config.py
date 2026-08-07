import os
import pytest
from unittest.mock import MagicMock, patch
from jiuwenswarm.common.local_env_config import (
    get_local_config,
    set_local_config,
    decrypt,
    encrypt,
    ENV_CONFIG_DICT,
)


class TestLocalEnvConfig:
    def setup_method(self):
        ENV_CONFIG_DICT.clear()

    def teardown_method(self):
        ENV_CONFIG_DICT.clear()

    def test_get_local_config_from_env_dict(self):
        ENV_CONFIG_DICT["TEST_KEY"] = "test_value"
        assert get_local_config("TEST_KEY") == "test_value"

    def test_get_local_config_from_os_environ(self):
        os.environ["TEST_KEY"] = "test_value"
        try:
            assert get_local_config("TEST_KEY") == "test_value"
        finally:
            del os.environ["TEST_KEY"]

    def test_get_local_config_default(self):
        assert get_local_config("NON_EXISTENT_KEY", "default") == "default"

    def test_set_local_config(self):
        set_local_config("TEST_KEY", "test_value")
        assert ENV_CONFIG_DICT["TEST_KEY"] == "test_value"

    def test_set_local_config_none_value(self):
        ENV_CONFIG_DICT["TEST_KEY"] = "test_value"
        set_local_config("TEST_KEY", None)
        assert "TEST_KEY" not in ENV_CONFIG_DICT

    def test_set_local_config_empty_string(self):
        ENV_CONFIG_DICT["TEST_KEY"] = "test_value"
        set_local_config("TEST_KEY", "")
        assert "TEST_KEY" not in ENV_CONFIG_DICT

    def test_set_local_config_zero_kept(self):
        set_local_config("TEST_KEY", 0)
        assert ENV_CONFIG_DICT["TEST_KEY"] == "0"

    def test_set_local_config_false_kept(self):
        set_local_config("TEST_KEY", False)
        assert ENV_CONFIG_DICT["TEST_KEY"] == "False"

    def test_decrypt_without_crypto_provider(self):
        result = decrypt("TEST_KEY", "cipher_text")
        assert result == "cipher_text"

    def test_decrypt_with_crypto_provider(self):
        mock_crypto = MagicMock()
        mock_crypto.decrypt.return_value = "decrypted_text"
        mock_registry = MagicMock()
        mock_registry.get_crypto_provider.return_value = mock_crypto
        
        with patch("jiuwenswarm.common.local_env_config.sys.modules") as mock_modules:
            mock_modules.get.return_value = MagicMock(ExtensionRegistry=MagicMock(get_instance=MagicMock(return_value=mock_registry)))
            result = decrypt("API_KEY", "cipher_text")
            assert result == "decrypted_text"
            mock_crypto.decrypt.assert_called_once_with("cipher_text")

    def test_decrypt_non_sensitive_key(self):
        mock_crypto = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get_crypto_provider.return_value = mock_crypto
        
        with patch("jiuwenswarm.common.local_env_config.sys.modules") as mock_modules:
            mock_modules.get.return_value = MagicMock(ExtensionRegistry=MagicMock(get_instance=MagicMock(return_value=mock_registry)))
            result = decrypt("NON_SENSITIVE_KEY", "plain_text")
            assert result == "plain_text"
            mock_crypto.decrypt.assert_not_called()

    def test_encrypt_without_crypto_provider(self):
        result = encrypt("TEST_KEY", "plain_text")
        assert result == "plain_text"

    def test_encrypt_with_crypto_provider(self):
        mock_crypto = MagicMock()
        mock_crypto.encrypt.return_value = "encrypted_text"
        mock_registry = MagicMock()
        mock_registry.get_crypto_provider.return_value = mock_crypto
        
        with patch("jiuwenswarm.common.local_env_config.sys.modules") as mock_modules:
            mock_modules.get.return_value = MagicMock(ExtensionRegistry=MagicMock(get_instance=MagicMock(return_value=mock_registry)))
            result = encrypt("API_KEY", "plain_text")
            assert result == "encrypted_text"
            mock_crypto.encrypt.assert_called_once_with("plain_text")

    def test_encrypt_non_sensitive_key(self):
        mock_crypto = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get_crypto_provider.return_value = mock_crypto
        
        with patch("jiuwenswarm.common.local_env_config.sys.modules") as mock_modules:
            mock_modules.get.return_value = MagicMock(ExtensionRegistry=MagicMock(get_instance=MagicMock(return_value=mock_registry)))
            result = encrypt("NON_SENSITIVE_KEY", "plain_text")
            assert result == "plain_text"
            mock_crypto.encrypt.assert_not_called()
