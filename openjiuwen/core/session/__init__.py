# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.session.checkpointer import Checkpointer
from openjiuwen.core.session.config.base import (
    Config,
    workflow_session_vars,
)
from openjiuwen.core.session.constants import (
    COMP_STREAM_CALL_TIMEOUT_KEY,
    END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY,
    END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY,
    FORCE_DEL_WORKFLOW_STATE_ENV_KEY,
    FORCE_DEL_WORKFLOW_STATE_KEY,
    LOOP_NUMBER_MAX_LIMIT_DEFAULT,
    LOOP_NUMBER_MAX_LIMIT_KEY,
    STREAM_INPUT_GEN_TIMEOUT_KEY,
    WORKFLOW_EXECUTE_TIMEOUT,
    WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT,
    WORKFLOW_STREAM_FRAME_TIMEOUT,
)
from openjiuwen.core.session.interaction.base import AgentInterrupt
from openjiuwen.core.session.interaction.interaction import InteractionOutput
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.internal.workflow import (
    NodeSession,
    SubWorkflowSession,
    WorkflowSession,
)
from openjiuwen.core.session.internal.wrapper import (
    RouterSession,
    WrappedSession,
)
from openjiuwen.core.session.session import (
    BaseSession,
    ProxySession
)
from openjiuwen.core.session.state.base import Transformer
from openjiuwen.core.session.state.workflow_state import CommitState
from openjiuwen.core.session.utils import (
    EndFrame,
    extract_origin_key,
    get_by_schema,
    get_value_by_nested_path,
    is_ref_path,
    NESTED_PATH_SPLIT,
)

from openjiuwen.core.session.session import Session

deprecated = ["Session"]

__all__ = [
    # session
    "BaseSession",
    "WrappedSession",
    "ProxySession",

    # workflow session
    "WorkflowSession",
    "NodeSession",
    "SubWorkflowSession",
    "RouterSession",
    "workflow_session_vars",

    # agent session
    "CommitState",

    # interaction
    "InteractiveInput",
    "InteractionOutput",
    "Checkpointer",
    "AgentInterrupt",

    # config
    "Config",

    # constants
    "COMP_STREAM_CALL_TIMEOUT_KEY",
    "WORKFLOW_EXECUTE_TIMEOUT",
    "WORKFLOW_STREAM_FRAME_TIMEOUT",
    "WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT",
    "END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY",
    "END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY",
    "LOOP_NUMBER_MAX_LIMIT_DEFAULT",
    "LOOP_NUMBER_MAX_LIMIT_KEY",
    "STREAM_INPUT_GEN_TIMEOUT_KEY",
    "FORCE_DEL_WORKFLOW_STATE_ENV_KEY",
    "FORCE_DEL_WORKFLOW_STATE_KEY",
    "NESTED_PATH_SPLIT",

    "EndFrame",
    "get_by_schema",
    "get_value_by_nested_path",
    "extract_origin_key",
    "is_ref_path",
    "Transformer",
] + deprecated
