# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import contextvars
import os
from abc import ABC
from copy import deepcopy
from typing import TypedDict, Any, Optional

from openjiuwen.core.common.logging import session_logger, LogEventType
from openjiuwen.core.session.constants import COMP_STREAM_CALL_TIMEOUT_KEY, STREAM_INPUT_GEN_TIMEOUT_KEY, \
    END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY, END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY, \
    WORKFLOW_EXECUTE_TIMEOUT, WORKFLOW_STREAM_FRAME_TIMEOUT, WORKFLOW_EXECUTE_TIMEOUT_ENV_KEY, \
    WORKFLOW_STREAM_FRAME_TIMEOUT_ENV_KEY, COMP_STREAM_CALL_TIMEOUT_ENV_KEY, STREAM_INPUT_GEN_TIMEOUT_ENV_KEY, \
    WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT, WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT_ENV_KEY, \
    LOOP_NUMBER_MAX_LIMIT_KEY, LOOP_NUMBER_MAX_LIMIT_ENV_KEY, LOOP_NUMBER_MAX_LIMIT_DEFAULT, \
    FORCE_DEL_WORKFLOW_STATE_ENV_KEY, FORCE_DEL_WORKFLOW_STATE_KEY

workflow_session_vars: contextvars.ContextVar[dict] = contextvars.ContextVar("workflow_session_vars", default={})


class MetadataLike(TypedDict):
    name: str
    event: str


_ENV_CONFIG_KEYS = [(WORKFLOW_EXECUTE_TIMEOUT_ENV_KEY, WORKFLOW_EXECUTE_TIMEOUT),
                    (WORKFLOW_STREAM_FRAME_TIMEOUT_ENV_KEY, WORKFLOW_STREAM_FRAME_TIMEOUT),
                    (WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT_ENV_KEY, WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT),
                    (COMP_STREAM_CALL_TIMEOUT_ENV_KEY, COMP_STREAM_CALL_TIMEOUT_KEY),
                    (STREAM_INPUT_GEN_TIMEOUT_ENV_KEY, STREAM_INPUT_GEN_TIMEOUT_KEY),
                    (LOOP_NUMBER_MAX_LIMIT_ENV_KEY, LOOP_NUMBER_MAX_LIMIT_KEY),
                    (FORCE_DEL_WORKFLOW_STATE_ENV_KEY, FORCE_DEL_WORKFLOW_STATE_KEY)]

_ENV_CONFIG_TYPES = {
    WORKFLOW_EXECUTE_TIMEOUT_ENV_KEY: 'float',
    WORKFLOW_STREAM_FRAME_TIMEOUT_ENV_KEY: 'float',
    WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT_ENV_KEY: 'float',
    COMP_STREAM_CALL_TIMEOUT_ENV_KEY: 'float',
    STREAM_INPUT_GEN_TIMEOUT_ENV_KEY: 'float',
    LOOP_NUMBER_MAX_LIMIT_ENV_KEY: 'int',
    FORCE_DEL_WORKFLOW_STATE_ENV_KEY: 'bool'
}


def _try_set_env(env_configs: dict, config_key: str, env_key: str, value):
    if value is not None:
        env_type = _ENV_CONFIG_TYPES.get(env_key, None)
        if env_type == 'float':
            try:
                env_configs[config_key] = float(value)
            except (ValueError, TypeError):
                session_logger.warning(
                    "Invalid float value for environment variable, using default",
                    event_type=LogEventType.SESSION_UPDATE,
                    metadata={"env_key": env_key, "value": value, "expected_type": "float"}
                )
        elif env_type == 'int':
            if isinstance(value, int):
                env_configs[config_key] = value
            elif isinstance(value, str):
                try:
                    env_configs[config_key] = int(value)
                except ValueError:
                    session_logger.warning(
                        "Invalid integer value for environment variable, using default",
                        event_type=LogEventType.SESSION_UPDATE,
                        metadata={"env_key": env_key, "value": value, "expected_type": "int"}
                    )
            else:
                session_logger.warning(
                    "Invalid integer value for environment variable, using default",
                    event_type=LogEventType.SESSION_UPDATE,
                    metadata={"env_key": env_key, "value": value, "expected_type": "int"}
                )
        elif env_type == 'bool':
            if isinstance(value, bool):
                env_configs[config_key] = value
            elif isinstance(value, str):
                env_value = value.lower()
                if env_value not in ['true', 'false']:
                    session_logger.warning(
                        "Invalid boolean value for environment variable, using default",
                        event_type=LogEventType.SESSION_UPDATE,
                        metadata={"env_key": env_key, "value": value, "expected_type": "bool"}
                    )
                else:
                    env_configs[config_key] = env_value == 'true'
            else:
                session_logger.warning(
                    "Invalid boolean value for environment variable, using default",
                    event_type=LogEventType.SESSION_UPDATE,
                    metadata={"env_key": env_key, "value": value, "expected_type": "bool"}
                )
        else:
            env_configs[config_key] = value


def _load_env_configs() -> dict:
    env_configs = {}

    for env_key, config_key in _ENV_CONFIG_KEYS:
        _try_set_env(env_configs, config_key, env_key, os.environ.get(env_key))
        _try_set_env(env_configs, config_key, env_key, workflow_session_vars.get().get(env_key))

    return env_configs


class Config(ABC):
    """
    Config is the class defines the basic infos of workflow
    """

    def __init__(self):
        """
        initialize the config
        """
        self._callback_metadata: dict[str, MetadataLike] = {}
        self._env: dict = {}
        self._workflow_configs: dict[str, "WorkflowConfig"] = {}
        self._agent_config: "AgentConfig" = None
        self._load_envs_()

    def set_envs(self, envs: dict[str, Any]) -> None:
        """
        set environment variables
        :param envs: envs
        """
        if not isinstance(envs, dict):
            return
        self._env.update(envs)

    def get_env(self, key: str, default: Any = None) -> Optional[Any]:
        """
        get environment variable by given key
        :param key: environment variable key
        :default key: environment variable default key
        :return: environment variable value
        """
        if key in self._env:
            return self._env[key]
        else:
            return default

    def get_envs(self):
        return deepcopy(self._env)

    def _load_envs_(self) -> None:
        self._load_builtin_configs_()

    def _load_builtin_configs_(self):
        builtin_configs = {
            COMP_STREAM_CALL_TIMEOUT_KEY: -1,
            STREAM_INPUT_GEN_TIMEOUT_KEY: -1,
            END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY: 5,
            END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY: 5,
            WORKFLOW_EXECUTE_TIMEOUT: 60,
            WORKFLOW_STREAM_FRAME_TIMEOUT: -1,
            WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT: -1,
            LOOP_NUMBER_MAX_LIMIT_KEY: LOOP_NUMBER_MAX_LIMIT_DEFAULT,
            FORCE_DEL_WORKFLOW_STATE_KEY: False
        }

        builtin_configs.update(_load_env_configs())

        self.set_envs(builtin_configs)

    def get_workflow_config(self, workflow_id):
        if workflow_id is None:
            raise ValueError("workflow_id is invalid, cannot be None")
        return self._workflow_configs.get(workflow_id)

    def get_agent_config(self):
        return self._agent_config

    def set_agent_config(self, agent_config):
        self._agent_config = agent_config

    def add_workflow_config(self, workflow_id, workflow_config):
        if workflow_id is None:
            raise ValueError("workflow_id is invalid, cannot be None")
        if workflow_config is None:
            raise ValueError("workflow config is invalid, cannot be None")
        self._workflow_configs[workflow_id] = workflow_config
