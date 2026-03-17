# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

WORKFLOW_EXECUTE_TIMEOUT = "_execute_timeout"
WORKFLOW_STREAM_FRAME_TIMEOUT = "_stream_frame_timeout"
WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT = "_stream_first_frame_timeout"

# transform/collect stream call timeout
COMP_STREAM_CALL_TIMEOUT_KEY = "_comp_stream_call_timeout"

# stream inputs' generator timeout
STREAM_INPUT_GEN_TIMEOUT_KEY = "_stream_input_generator_timeout"

# End Component template config environments field
END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY = "_end_comp_template_render_position_timeout"
END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY = "_end_comp_template_branch_render_timeout"
# Loop Component max number limit
LOOP_NUMBER_MAX_LIMIT_KEY = "_loop_number_max_limit"
LOOP_NUMBER_MAX_LIMIT_DEFAULT = 1000

# checkpointer control
FORCE_DEL_WORKFLOW_STATE_KEY = "_force_del_workflow_state"

# env
WORKFLOW_EXECUTE_TIMEOUT_ENV_KEY = "WORKFLOW_EXECUTE_TIMEOUT"
WORKFLOW_STREAM_FRAME_TIMEOUT_ENV_KEY = "WORKFLOW_STREAM_FRAME_TIMEOUT"
WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT_ENV_KEY = "WORKFLOW_STREAM_FIRST_FRAME_TIMEOUT"
COMP_STREAM_CALL_TIMEOUT_ENV_KEY = "COMP_STREAM_CALL_TIMEOUT"
STREAM_INPUT_GEN_TIMEOUT_ENV_KEY = "STREAM_INPUT_GEN_TIMEOUT"
LOOP_NUMBER_MAX_LIMIT_ENV_KEY = "LOOP_NUMBER_MAX_LIMIT"
FORCE_DEL_WORKFLOW_STATE_ENV_KEY = "FORCE_DEL_WORKFLOW_STATE"