"""
版权所有 (c) 华为技术有限公司 2026-2026
"""

import json
import os

from constants import CONFIG_FILE_NAME, ENV_FILE_PATH, REQUIRED_ENV_VARS


class EnvConfig:
    def __init__(self):
        self.apiKey = ''
        self.sandbox_id = ''
        self.serviceUrl = ''
        self.uid = ''


class FileConfig:
    def __init__(self):
        self.api_timeout = 5000
        self.skillId = 'skill-scope'
        self.requestFrom = 'cloudSandbox'
        self.textSource = 'question'
        self.action = 'SKILL_SCAN'


_cached_env_config = None
_cached_file_config = None


def load_env_config() -> EnvConfig:
    global _cached_env_config
    if _cached_env_config:
        return _cached_env_config

    config = EnvConfig()

    if not os.path.exists(ENV_FILE_PATH):
        raise Exception(f'Environment file not found: {ENV_FILE_PATH}')

    try:
        with open(ENV_FILE_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                trimmed = line.strip()
                if not trimmed or trimmed.startswith('#'):
                    continue
                parts = trimmed.split('=', 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip()
                    if key == 'PERSONAL-API-KEY':
                        config.apiKey = value
                    elif key == 'SANDBOX-ID':
                        config.sandbox_id = value
                    elif key == 'SERVICE_URL':
                        config.serviceUrl = value
                    elif key == 'PERSONAL-UID':
                        config.uid = value
    except Exception as e:
        raise Exception(f'Failed to read environment file: {e}')

    # 校验必填项
    if not config.apiKey:
        raise Exception("Missing or empty 'PERSONAL-API-KEY' in env files")
    if not config.uid:
        raise Exception("Missing or empty 'PERSONAL-UID' in env files")
    if not config.serviceUrl:
        raise Exception("Missing or empty 'SERVICE_URL' in env files")

    _cached_env_config = config
    return _cached_env_config


def load_file_config() -> FileConfig:
    global _cached_file_config
    if _cached_file_config:
        return _cached_file_config

    config_path = os.path.join(os.path.dirname(__file__), CONFIG_FILE_NAME)

    if not os.path.exists(config_path):
        raise Exception(f'Config file not found: {CONFIG_FILE_NAME}')

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        raise Exception(f'Failed to read/parse config file {CONFIG_FILE_NAME}: {e}')

    config = FileConfig()

    if 'api' in data and 'timeout' in data['api']:
        config.api_timeout = data['api']['timeout']
    if 'skillId' in data:
        config.skillId = data['skillId']
    if 'requestFrom' in data:
        config.requestFrom = data['requestFrom']
    if 'textSource' in data:
        config.textSource = data['textSource']
    if 'action' in data:
        config.action = data['action']

    _cached_file_config = config
    return _cached_file_config
