# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from enum import Enum


class StatusCode(Enum):
    """Unified StatusCode enum (linted & normalized)"""

    SUCCESS = (0, "success")
    ERROR = (-1, "error")

    # =============================================================================================================
    # 100. Workflow 100000–100999
    # =============================================================================================================

    # ===============================================
    # 0. Workflow Validation Error Codes (100000 - 100099)
    # All errors in this section raise ValidateError when triggered.
    # These errors occur during workflow configuration/creation phase.
    # ===============================================
    WORKFLOW_COMPONENT_ID_INVALID = (
        100010, "the component id is invalid for component '{comp_id}', reason='{reason}', workflow='{workflow}'")
    WORKFLOW_COMPONENT_ABILITY_INVALID = (
        100011, "the ability is invalid for component '{comp_id}', ability={ability}, reason='{reason}', "
                "workflow='{workflow}'")
    WORKFLOW_EDGE_INVALID = (
        100012, "edge is invalid, reason='{reason}', source='{src_cmp_id}', target='{target_cmp_id}', "
                "workflow='{workflow}'")
    WORKFLOW_CONDITION_EDGE_INVALID = (
        100013, "condition edge is invalid, reason='{reason}'. source='{src_cmp_id}', workflow='{workflow}'")
    WORKFLOW_COMPONENT_SCHEMA_INVALID = (
        100014, "component input/output schema is invalid for component '{comp_id}', reason='{reason}', "
                "workflow='{workflow}'")

    WORKFLOW_STREAM_EDGE_INVALID = (
        100015, "stream edge is invalid, reason='{reason}', source='{src_cmp_id}', target='{target_cmp_id}', "
                "workflow='{workflow}'")
    WORKFLOW_EXECUTE_INPUT_INVALID = (
        100016, "workflow execute input is invalid, inputs='{inputs}', reason='{reason}', workflow='{workflow}'")
    WORKFLOW_EXECUTE_SESSION_INVALID = (100017, "execute session is invalid, reason='{reason}', workflow='{workflow}'")

    # ===============================================================
    # 1. Workflow Execution Error Codes (100100 - 100199)a
    # All errors in this section raise ExecutionError when triggered.
    # These errors occur during workflow runtime/execution phase.
    # ===============================================================
    WORKFLOW_COMPILE_ERROR = (
        100100,
        "workflow compilation has error, error='{reason}', workflow={workflow}")
    WORKFLOW_EXECUTION_TIMEOUT = (
        100101,
        "workflow execution exceeded time limit of {timeout} seconds, workflow='{workflow}'")
    WORKFLOW_EXECUTION_ERROR = (
        100102,
        "workflow execution has error, error='{reason}', workflow='{workflow}'")

    # ===============================================================
    # 2. Workflow Component orchestration Error Codes (100200 - 100299)
    # All errors in this section raise ExecutionError when triggered.
    # These errors occur during workflow runtime/execution phase.
    # ===============================================================
    WORKFLOW_INNER_ORCHESTRATION_ERROR = (
        100053, "workflow inner orchestration error, error='{reason}'")
    WORKFLOW_COMPONENT_EXECUTION_ERROR = (
        100054, "component '{comp}' execute '{ability}' error, reason='{reason}', workflow='{workflow}'")

    # =============================================================================================================
    # 101. Built-in Workflow Component 101000–101999
    # =============================================================================================================

    # ======================================
    # 00. Start Component  101000 - 101009
    # ======================================

    # ======================================
    # 01. End Component    101010 - 101019
    # ======================================
    COMPONENT_END_PARAM_INVALID = (100010, "component end params is invalid, error='{reason}'")

    # ======================================
    # 02. BranchComponent  101020 - 101029
    # ======================================
    COMPONENT_BRANCH_PARAM_INVALID = (101020, "component branch params is invalid, error='{reason}'")
    COMPONENT_BRANCH_EXECUTION_ERROR = (101021, "component branch execution error, error='{reason}'")

    # expression
    EXPRESSION_SYNTAX_ERROR = (101024, "expression syntax error")
    EXPRESSION_EVAL_ERROR = (101025, "expression evaluation error, reason: {error_msg}")
    ARRAY_CONDITION_ERROR = (101026, "array condition error")
    NUMBER_CONDITION_ERROR = (101027, "number condition error, reason: {error_msg}")

    # ======================================
    # 03. LoopComponent  101030 - 101049
    # ======================================
    COMPONENT_LOOP_GROUP_PARAM_INVALID = (101030, "loop group params is invalid, error='{reason}'")
    COMPONENT_LOOP_SET_VAR_PARAM_INVALID = (101031, "loop set_var params invalid, error='{reason}'")

    COMPONENT_LOOP_EXECUTION_ERROR = (101040, "loop execution error, error='{reason}', comp='{comp}'")
    COMPONENT_LOOP_CONDITION_EXECUTION_ERROR = (
        101041, "loop condition execution error, error='{reason}', comp='{comp}'")
    COMPONENT_LOOP_BREAK_EXECUTION_ERROR = (101042, "loop break execution error, error='{reason}', comp='{comp}'")
    COMPONENT_LOOP_SET_VAR_EXECUTION_ERROR = (101043, "loop set_var execution error, error='{reason}', comp='{comp}'")

    # =========================================
    # 05. SubWorkflowComponent  101150 - 101159
    # =========================================
    COMPONENT_SUB_WORKFLOW_PARAM_INVALID = (101150, "component sub_workflow param is invalid, error='{reason}'")

    ## LLMComponent  101000 - 101049
    COMPONENT_LLM_TEMPLATE_CONFIG_ERROR = (101000, "component llm_template config error, reason: {error_msg}")
    COMPONENT_LLM_RESPONSE_CONFIG_INVALID = (101001, "component llm_response_config is invalid, reason: {error_msg}")
    COMPONENT_LLM_CONFIG_ERROR = (101002, "component llm config error, reason: {error_msg}")
    COMPONENT_LLM_INVOKE_CALL_FAILED = (101003, "component llm_invoke call failed, reason: {error_msg}")
    COMPONENT_LLM_EXECUTION_PROCESS_ERROR = (101004, "component llm_execution process error, reason: {error_msg}")
    COMPONENT_LLM_INIT_FAILED = (101005, "component llm initialization failed, reason: {error_msg}")
    COMPONENT_LLM_TEMPLATE_PROCESS_ERROR = (101006, "component llm_template process error, reason: {error_msg}")
    COMPONENT_LLM_CONFIG_INVALID = (101007, "component llm_config is invalid, reason: {error_msg}")

    ## IntentDetectionComponent 101050 - 101069
    COMPONENT_INTENT_DETECTION_INPUT_PARAM_ERROR = (
        101050,
        "component intent_detection_input parameter error, reason: {error_msg}")
    COMPONENT_INTENT_DETECTION_LLM_INIT_FAILED = (
        101051,
        "component intent_detection_llm initialization failed, reason: {error_msg}")
    COMPONENT_INTENT_DETECTION_INVOKE_CALL_FAILED = (
        101052,
        "component intent_detection_invoke call failed, reason: {error_msg}")

    ## QuestionComponent 101070 - 101099
    COMPONENT_QUESTIONER_INPUT_PARAM_ERROR = (101070,
                                              "component questioner_input parameter error, reason: {error_msg}")
    COMPONENT_QUESTIONER_CONFIG_ERROR = (101071, "component questioner config error, reason: {error_msg}")
    COMPONENT_QUESTIONER_INPUT_INVALID = (101072, "component questioner_input is invalid, reason: {error_msg}")
    COMPONENT_QUESTIONER_STATE_INIT_FAILED = (101073,
                                              "component questioner_state initialization failed, reason: {error_msg}")
    COMPONENT_QUESTIONER_RUNTIME_ERROR = (101074, "component questioner runtime error, reason: {error_msg}")
    COMPONENT_QUESTIONER_INVOKE_CALL_FAILED = (101075, "component questioner_invoke call failed, reason: {error_msg}")
    COMPONENT_QUESTIONER_EXECUTION_PROCESS_ERROR = (101076,
                                                    "component questioner_execution process error, reason: {error_msg}")

    ## KnowledgeRetrievalComponent 101100 - 101149
    COMPONENT_KNOWLEDGE_RETRIEVAL_INVOKE_CALL_FAILED = (
        101100, "component knowledge_retrieval invoke call failed, reason: {error_msg}")
    COMPONENT_KNOWLEDGE_RETRIEVAL_EMBED_MODEL_INIT_ERROR = (
        101101, "component knowledge_retrieval embed_model initialization error, reason: {error_msg}")
    COMPONENT_KNOWLEDGE_RETRIEVAL_INPUT_PARAM_ERROR = (
        101102, "component knowledge_retrieval input parameter error, reason: {error_msg}")
    COMPONENT_KNOWLEDGE_RETRIEVAL_LLM_MODEL_INIT_ERROR = (
        101103, "component knowledge_retrieval llm_model initialization failed, reason: {error_msg}")
    
    ## ToolComponent  102000 - 102019
    COMPONENT_TOOL_EXECUTION_ERROR = (102000, "component tool execution error, reason: {error_msg}")
    COMPONENT_TOOL_INPUT_PARAM_ERROR = (102001, "component tool_input parameter error, reason: {error_msg}")
    COMPONENT_TOOL_INIT_FAILED = (102002, "component tool initialization failed, reason: {error_msg}")

    # =========================
    # Agent Orchestration 120000–129999
    # =========================

    # Agent Orchestration - ReAct Agent Orchestration And Execution 120000 - 120999
    AGENT_TOOL_NOT_FOUND = (120000, "agent tool not found, reason: {error_msg}")
    AGENT_TOOL_EXECUTION_ERROR = (120001, "agent tool execution error, reason: {error_msg}")
    AGENT_TASK_NOT_SUPPORT = (120002, "agent task is not supported, reason: {error_msg}")
    AGENT_WORKFLOW_EXECUTION_ERROR = (120003, "agent workflow execution error, reason: {error_msg}")
    AGENT_PROMPT_PARAM_ERROR = (120004, "agent prompt parameter error, reason: {error_msg}")

    # Agent Orchestration - Workflow Agent Orchestration And Execution 121000 - 121999
    # Agent Orchestration - Custom Agent Interface 122000 - 122999

    # Agent Controller 123000 - 123999
    AGENT_CONTROLLER_INVOKE_CALL_FAILED = (123000, "agent controller_invoke call failed, reason: {error_msg}")
    AGENT_SUB_TASK_TYPE_NOT_SUPPORT = (123001, "agent sub_task_type is not supported, reason: {error_msg}")
    AGENT_CONTROLLER_USER_INPUT_PROCESS_ERROR = (
        123002,
        "agent controller_user_input process error, reason: {error_msg}")
    AGENT_CONTROLLER_RUNTIME_ERROR = (123003, "agent controller runtime error, reason: {error_msg}")
    AGENT_CONTROLLER_EXECUTION_CALL_FAILED = (
        123004,
        "agent controller_execution call failed, reason: {error_msg}")
    AGENT_CONTROLLER_TOOL_EXECUTION_PROCESS_ERROR = (
        123005,
        "agent controller_tool_execution process error, reason: {error_msg}")
    AGENT_CONTROLLER_TASK_PARAM_ERROR = (123006, "controller task parameter error, reason: {error_msg}")
    AGENT_CONTROLLER_INTENT_PARAM_ERROR = (123007, "controller intention parameter error, reason: {error_msg}")
    AGENT_CONTROLLER_TASK_EXECUTION_ERROR = (123008, "controller task execution error, reason: {error_msg}")
    AGENT_CONTROLLER_EVENT_HANDLER_ERROR = (123009, "controller event handler error, reason: {error_msg}")
    AGENT_CONTROLLER_EVENT_QUEUE_ERROR = (123010, "agent controller event queue execution error, reason: {error_msg}")

    # DeepAgent 123020 - 123039
    DEEPAGENT_CONFIG_PARAM_ERROR = (123020, "deepagent config parameter error, reason: {error_msg}")
    DEEPAGENT_INPUT_PARAM_ERROR = (123021, "deepagent input parameter error, reason: {error_msg}")
    DEEPAGENT_CONTEXT_PARAM_ERROR = (123022, "deepagent callback context parameter error, reason: {error_msg}")
    DEEPAGENT_RUNTIME_ERROR = (123023, "deepagent runtime error, reason: {error_msg}")
    DEEPAGENT_TASK_LOOP_NOT_IMPLEMENTED = (123024, "deepagent task loop not implemented, reason: {error_msg}")

    # =============================================================================================================
    # 110 Runner / Distributed 110000–110999
    # =============================================================================================================

    # =======================================
    # 0. Runner execution 110000 - 110099
    # =======================================
    RUNNER_TERMINATION_ERROR = (110002, "runner is already terminate")
    RUNNER_RUN_AGENT_ERROR = (110022, "runner run agent '{agent}' failed, error='{reason}'")



    # =======================================
    # 1. distribute execution 110100 - 110199
    # =======================================
    REMOTE_AGENT_EXECUTION_TIMEOUT = (110100, "remote agent '{agent_id}' execute exceed {timeout} seconds")
    REMOTE_AGENT_EXECUTION_ERROR = (110101, "remote agent '{agent_id}' execute error, error='{reason}'")
    REMOTE_AGENT_RESPONSE_PROCESS_ERROR = (
        110102,
        "remote agent request process error, message_id='{message_id}', process_id='{process_id}', "
        "response='{code={error_code}', msg='{error_msg}'")

    # =======================================
    # 2. message queue 110200 - 110299
    # =======================================
    MESSAGE_QUEUE_INITIATION_ERROR = (110200, "init type '{type}' message queue error, error='{reason}'")
    MESSAGE_QUEUE_TOPIC_SUBSCRIPTION_ERROR = (110210, "subscribe topic error, topic='{topic}', error='{reason}'")
    MESSAGE_QUEUE_TOPIC_MESSAGE_PRODUCTION_ERROR = (
        110211,
        "produce message error, topic='{topic}', message='{message}', error='{reason}'")
    MESSAGE_QUEUE_MESSAGE_CONSUME_ERROR = (110212, "consume message error, error='{reason}'")
    MESSAGE_QUEUE_MESSAGE_PROCESS_EXECUTION_ERROR = (110213, "process message error, error='{reason}'")

    # =======================================
    # 3. dist message queue 110300 - 110399
    # =======================================
    DIST_MESSAGE_QUEUE_CLIENT_START_ERROR = (110300, "distribute message queue client start error, error='{reason}'")

    # =======================================
    # 4. resource_manager 110400 - 110599
    # =======================================
    # ResourceMgr 134200-134300
    # param validate
    RESOURCE_ID_VALUE_INVALID = (110400, "{resource_type} id is invalid, reason='{reason}'")
    RESOURCE_TAG_VALUE_INVALID = (110401, "tag is invalid, tag={tag}, reason='{reason}'")
    RESOURCE_CARD_VALUE_INVALID = (110402, "{resource_type} card is invalid, reason='{reason}'")
    RESOURCE_PROVIDER_INVALID = (110403, "{resource_type} provider is invalid, reason='{reason}'")
    RESOURCE_VALUE_INVALID = (110404, "{resource_type} value is invalid, reason='{reason}'")

    RESOURCE_ADD_ERROR = (110430, "resource add failed, card='{card}', error='{reason}'")

    # tag manager error 110480 - 110499
    RESOURCE_TAG_REMOVE_TAG_ERROR = (110480, "tag is invalid, tag='{tag}', error='{reason}'")
    RESOURCE_TAG_ADD_RESOURCE_TAG_ERROR = (
        110481,
        "add tag failed, resource_id='{resource_id}', tag='{tag}', error='{reason}'")
    RESOURCE_TAG_REMOVE_RESOURCE_TAG_ERROR = (
        110482,
        "remove resource tag failed, resource_id='{resource_id}', tags='{tags}', error='{reason}'")
    RESOURCE_TAG_REPLACE_RESOURCE_TAG_ERROR = (
        110483,
        "replace resource tag failed, resource_id='{resource_id}', tags='{tags}', error='{reason}'")
    RESOURCE_TAG_FIND_RESOURCE_ERROR = (
        110484,
        "replace resource tag failed, resource_id='{resource_id}', tags='{tags}', error='{reason}'")

    # specific_resource error 110500 - 110599
    # mcp resource error 110510 - 110519
    RESOURCE_MCP_SERVER_PARAM_INVALID = (
        110510,
        "server param is invalid, server_config='{server_config}', error='{reason}'")

    RESOURCE_MCP_SERVER_CONNECTION_ERROR = (
        110511,
        "mcp server connect failed, server_config={server_config}, error='{reason}'")
    RESOURCE_MCP_SERVER_ADD_ERROR = (
        110512, "mcp server add failed, server_config={server_config}, error='{reason}'")
    RESOURCE_MCP_SERVER_REFRESH_ERROR = (
        110513, "mcp server refresh failed, server_id={server_id}, error='{reason}'")
    RESOURCE_MCP_SERVER_REMOVE_ERROR = (
        110514, "mcp server remove failed, server_id={server_id}, error='{reason}'")
    RESOURCE_MCP_TOOL_GET_ERROR = (
        110515, "mcp server tool get failed, server_id={server_id}, error='{reason}'")

    # =======================================
    # 5. callback framework 110600 - 110699
    # =======================================
    CALLBACK_EXECUTION_ABORTED = (110600, "callback execution aborted: {reason}")

    # =============================================================================================================
    # 111. Session 111000 – 111999
    # =============================================================================================================

    # =======================================
    # 01. component session 111000 - 111009
    # =======================================
    COMP_SESSION_INTERACT_ERROR = (
        111005,
        "interact is not support, error='{reason}', comp_id={comp_id}, workflow={workflow}")

    # =======================================
    # 11. interaction     111110 - 111119
    # =======================================
    INTERACTION_INPUT_INVALID = (111110, "interaction input is invalid, reason={reason}")

    # =======================================
    # 12 checkpointer     111120 - 111129
    # =======================================
    CHECKPOINTER_POST_WORKFLOW_EXECUTION_ERROR = (
        111120,
        "post workflow execute error, session_id={session_id}, workflow={workflow}, error='{reason}'")
    CHECKPOINTER_PRE_WORKFLOW_EXECUTION_ERROR = (
        111121,
        "pre workflow execute error, session_id={session_id}, workflow={workflow}, error='{reason}'")
    CHECKPOINTER_INTERRUPT_AGENT_ERROR = (
        111122,
        "interrupt agent execute error, session_id={session_id}, agent={agent}, error='{reason}'")
    CHECKPOINTER_POST_AGENT_EXECUTION_ERROR = (
        111123,
        "post agent execute error, session_id={session_id}, agent={agent}, error='{reason}'")
    CHECKPOINTER_CONFIG_ERROR = (
        111124,
        "checkpointer config error, session_id={session_id}, error='{reason}'")

    # =======================================
    # 13 stream writer     111130 - 111139
    # =======================================
    STREAM_WRITER_MANAGER_ADD_WRITER_ERROR = (111130, "add new stream writer error, mode={mode}, error='{reason}'")
    STREAM_WRITER_MANAGER_REMOVE_WRITER_ERROR = (111131, "remove stream writer error, mode={mode}, error='{reason}'")
    STREAM_WRITER_WRITE_STREAM_VALIDATION_ERROR = (
        111132,
        "writer stream data validate error, stream_type={schema_type}, "
        "stream_data={stream_data}, error='{reason}'")
    STREAM_WRITER_WRITE_STREAM_ERROR = (
        111133, "writer stream data error, stream_data={stream_data}, error='{reason}'")

    STREAM_OUTPUT_FIRST_CHUNK_INTERVAL_TIMEOUT = (
        111134,
        "stream output first stream chunk timeout, timeout={timeout}s, error='{reason}'")
    STREAM_OUTPUT_CHUNK_INTERVAL_TIMEOUT = (
        111135,
        "stream output next stream chunk timeout, interval_timeout={timeout}s, error='{reason}'")

    # =======================================
    # 14 tracer     111140 - 111149
    # =======================================
    TRACER_WORKFLOW_TRACE_ERROR = (111140, "trace workflow error, error='{reason}'")
    TRACER_AGENT_TRACE_ERROR = (111141, "trace agent error, error='{reason}'")

    # =============================================================================================================
    # 112. Graph Engine 112000–112999
    # =============================================================================================================
    # 01. graph state commit 112030 - 112039
    GRAPH_STATE_COMMIT_ERROR = (112030, "graph commit state error, error='{reason}'")

    # 02. drawable graph 112020 - 112029
    DRAWABLE_GRAPH_START_NODE_INVALID = (
        112020,
        "drawable_graph start node is invalid, node={node_id}, reason={reason}",
    )
    DRAWABLE_GRAPH_END_NODE_INVALID = (
        112021,
        "drawable_graph end node is invalid, node={node_id}, reason={reason}",
    )
    DRAWABLE_GRAPH_BREAK_NODE_INVALID = (
        112022,
        "drawable_graph break node is invalid, node={node_id}, reason={reason}",
    )
    DRAWABLE_GRAPH_TO_MERMAID_INVALID = (112043, "drawable_graph to_mermaid error, reason={reason}")

    # 03. stream graph execution 112030 - 112049
    GRAPH_STREAM_ACTOR_EXECUTION_ERROR = (112030, "actor manager execute error, error='{reason}'")
    # 05. graph vertex execution 112050 - 112069
    GRAPH_VERTEX_EXECUTION_ERROR = (112050, "vertex execute error, error='{reason}', node_id={node_id}")
    GRAPH_VERTEX_STREAM_CALL_TIMEOUT = (112051, "vertex stream timeout, timeout={timeout}, node_id={node_id}")
    GRAPH_VERTEX_STREAM_CALL_ERROR = (112052, "vertex stream call error, error='{reason}', node_id={node_id}")

    # 10. pregel graph 112100 - 112199
    PREGEL_GRAPH_NODE_ID_INVALID = (112100, "node id is invalid, node_id={node_id}, error='{reason}'")
    PREGEL_GRAPH_NODE_INVALID = (112101, "node is invalid, node_id={node_id}, error='{reason}'")
    PREGEL_GRAPH_EDGE_INVALID = (112102,
                                 "edge is invalid, source_id={source_id}, target_id={target_id}, error='{reason}'")
    PREGEL_GRAPH_CONDITION_EDGE_INVALID = (112103, "condition edge is invalid, source_id={source_id}, error='{reason}'")

    # =========================
    # Multi-Agent 130000 - 130999
    # =========================

    AGENT_TEAM_ADD_RUNTIME_ERROR = (132000, "agent team_add runtime error, reason: {error_msg}")
    AGENT_TEAM_CREATE_RUNTIME_ERROR = (132001, "agent team_create runtime error, reason: {error_msg}")
    AGENT_TEAM_EXECUTION_ERROR = (132002, "agent team execution error, reason: {error_msg}")
    AGENT_TEAM_AGENT_NOT_FOUND = (132003, "agent team agent not found error, reason: {error_msg}")

    # =========================
    # DevTools / AgentBuilder 140000 - 140099
    # =========================

    # AgentBuilder - Clarifier 140000 - 140029
    AGENT_BUILDER_RESOURCE_PARSE_ERROR = (
        140000, "agent builder resource parse error, resource_type='{resource_type}', error='{reason}'")
    AGENT_BUILDER_LLM_SERVICE_ERROR = (
        140001, "agent builder llm service error, error='{reason}'")

    # AgentBuilder - Generator 140030 - 140059
    AGENT_BUILDER_GENERATOR_PARSE_ERROR = (
        140030, "agent builder generator parse error, error='{reason}'")

    # AgentBuilder - Transformer 140060 - 140099
    AGENT_BUILDER_TRANSFORMER_ERROR = (
        140060, "agent builder transformer error, error='{reason}'")

    
    WORKFLOW_DL_GENERATION_ERROR = (
        140061,
        "workflow dl generation error, reason: {error_msg}")
    WORKFLOW_INTENTION_DETECT_ERROR = (
        140062,
        "workflow intention detect error, reason: {error_msg}")
    LLM_AGENT_STATE_ERROR = (
        140063,
        "llm agent state error, reason: {error_msg}")

    # =========================
    # ContextEngine 150000 - 154999
    # =========================

    CONTEXT_MESSAGE_PROCESS_ERROR = (153000, "context message process error, reason: {error_msg}")
    CONTEXT_EXECUTION_ERROR = (153001, "context execution execution error, reason: {error_msg}")
    CONTEXT_MESSAGE_INVALID = (153003, "context message is invalid, reason: {error_msg}")

    # =========================
    # KnowledgeBase Retrieval 155000 - 157999
    # =========================

    # KnowledgeBase Retrieval - Embedding 155000 - 155099
    RETRIEVAL_EMBEDDING_INPUT_INVALID = (155000, "retrieval embedding_input is invalid, reason: {error_msg}")
    RETRIEVAL_EMBEDDING_MODEL_NOT_FOUND = (155001, "retrieval embedding_model not found, reason: {error_msg}")
    RETRIEVAL_EMBEDDING_CALL_FAILED = (155002, "retrieval embedding call failed, reason: {error_msg}")
    RETRIEVAL_EMBEDDING_RESPONSE_INVALID = (155003, "retrieval embedding_response is invalid, reason: {error_msg}")
    RETRIEVAL_EMBEDDING_REQUEST_CALL_FAILED = (
        155004,
        "retrieval embedding_request call failed, reason: {error_msg}",
    )
    RETRIEVAL_EMBEDDING_UNREACHABLE_CALL_FAILED = (155005, "retrieval embedding call failed, reason: {error_msg}")
    RETRIEVAL_EMBEDDING_CALLBACK_INVALID = (155006, "retrieval embedding_callback is invalid, reason: {error_msg}")

    # KnowledgeBase Retrieval - Indexing 155100 - 155199
    RETRIEVAL_INDEXING_CHUNK_SIZE_INVALID = (155100, "retrieval indexing_chunk_size is invalid, reason: {error_msg}")
    RETRIEVAL_INDEXING_CHUNK_OVERLAP_INVALID = (
        155101,
        "retrieval indexing_chunk_overlap is invalid, reason: {error_msg}",
    )
    RETRIEVAL_INDEXING_TOKENIZER_PROCESS_ERROR = (
        155102,
        "retrieval indexing_tokenizer process error, reason: {error_msg}",
    )
    RETRIEVAL_INDEXING_FILE_NOT_FOUND = (155103, "retrieval indexing_file not found, reason: {error_msg}")
    RETRIEVAL_INDEXING_FORMAT_NOT_SUPPORT = (155104, "retrieval indexing_format is not supported, reason: {error_msg}")
    RETRIEVAL_INDEXING_EMBED_MODEL_NOT_FOUND = (155105, "retrieval indexing_embed_model not found, reason: {error_msg}")
    RETRIEVAL_INDEXING_DIMENSION_NOT_FOUND = (155106, "retrieval indexing_dimension not found, reason: {error_msg}")
    RETRIEVAL_INDEXING_PATH_NOT_FOUND = (155107, "retrieval indexing_path not found, reason: {error_msg}")
    RETRIEVAL_INDEXING_ADD_DOC_RUNTIME_ERROR = (155108, "retrieval indexing_add_doc runtime error, reason: {error_msg}")
    RETRIEVAL_INDEXING_VECTOR_FIELD_INVALID = (
        155109,
        "retrieval indexing_vector_field is invalid, reason: {error_msg}",
    )
    RETRIEVAL_INDEXING_FETCH_ERROR = (
        155110,
        "retrieval indexing fetch or parse error, reason: {error_msg}",
    )

    # KnowledgeBase Retrieval - Retriever 155200 - 155299
    RETRIEVAL_RETRIEVER_MODE_NOT_SUPPORT = (155200, "retrieval retriever_mode is not supported, reason: {error_msg}")
    RETRIEVAL_RETRIEVER_SCORE_THRESHOLD_INVALID = (
        155201,
        "retrieval retriever_score_threshold is invalid, reason: {error_msg}",
    )
    RETRIEVAL_RETRIEVER_EMBED_MODEL_NOT_FOUND = (
        155202,
        "retrieval retriever_embed_model not found, reason: {error_msg}",
    )
    RETRIEVAL_RETRIEVER_INDEX_TYPE_NOT_SUPPORT = (
        155203,
        "retrieval retriever_index_type is not supported, reason: {error_msg}",
    )
    RETRIEVAL_RETRIEVER_MODE_INVALID = (155204, "retrieval retriever_mode is invalid, reason: {error_msg}")
    RETRIEVAL_RETRIEVER_CAPABILITY_NOT_SUPPORT = (
        155205,
        "retrieval retriever_capability is not supported, reason: {error_msg}",
    )
    RETRIEVAL_RETRIEVER_VECTOR_STORE_NOT_FOUND = (
        155206,
        "retrieval retriever_vector_store not found, reason: {error_msg}",
    )
    RETRIEVAL_RETRIEVER_COLLECTION_NOT_FOUND = (
        155207, "retrieval retriever_collection not found, reason: {error_msg}")
    RETRIEVAL_RETRIEVER_NOT_FOUND = (
        155208,
        "retrieval retriever not found, reason: {error_msg}",
    )
    RETRIEVAL_RETRIEVER_LLM_CLIENT_NOT_FOUND = (
        155209, "retrieval retriever_llm_client not found, reason: {error_msg}")
    RETRIEVAL_RETRIEVER_TOP_K_INVALID = (
        155210, "retrieval retriever_top_k is invalid, reason: {error_msg}")
    RETRIEVAL_RETRIEVER_INVALID = (
        155211, "retrieval retriever is invalid, reason: {error_msg}")

    # KnowledgeBase Retrieval - Utils 155300 - 155399
    RETRIEVAL_UTILS_CONFIG_FILE_NOT_FOUND = (155300, "retrieval utils_config_file not found, reason: {error_msg}")
    RETRIEVAL_UTILS_PYYAML_NOT_FOUND = (155301, "retrieval utils_pyyaml not found, reason: {error_msg}")
    RETRIEVAL_UTILS_CONFIG_FORMAT_NOT_SUPPORT = (
        155302,
        "retrieval utils_config_format is not supported, reason: {error_msg}",
    )
    RETRIEVAL_UTILS_CONFIG_NOT_FOUND = (155303, "retrieval utils_config not found, reason: {error_msg}")
    RETRIEVAL_UTILS_CONFIG_PROCESS_ERROR = (155304, "retrieval utils_config process error, reason: {error_msg}")

    # KnowledgeBase Retrieval - Vector Store 155400 - 155499
    RETRIEVAL_VECTOR_STORE_PATH_NOT_FOUND = (155400, "retrieval vector_store_path not found, reason: {error_msg}")
    RETRIEVAL_VECTOR_STORE_QUERY_INVALID = (155401, "retrieval vector_store_query not valid, reason: {error_msg}")
    RETRIEVAL_VECTOR_STORE_PROVIDER_INVALID = (
        155402, "retrieval vector_store_provider is not supported, reason: {error_msg}")

    # KnowledgeBase Retrieval - Knowledge Base 155500 - 155599
    RETRIEVAL_KB_PARSER_NOT_FOUND = (155500, "retrieval kb_parser not found, reason: {error_msg}")
    RETRIEVAL_KB_CHUNKER_NOT_FOUND = (155501, "retrieval kb_chunker not found, reason: {error_msg}")
    RETRIEVAL_KB_INDEX_MANAGER_NOT_FOUND = (155502, "retrieval kb_index_manager not found, reason: {error_msg}")
    RETRIEVAL_KB_VECTOR_STORE_NOT_FOUND = (155503, "retrieval kb_vector_store not found, reason: {error_msg}")
    RETRIEVAL_KB_INDEX_BUILD_EXECUTION_ERROR = (155504, "retrieval kb_index_build execution error, reason: {error_msg}")
    RETRIEVAL_KB_CHUNK_INDEX_BUILD_EXECUTION_ERROR = (
        155505,
        "retrieval kb_chunk_index_build execution error, reason: {error_msg}",
    )
    RETRIEVAL_KB_TRIPLE_INDEX_BUILD_EXECUTION_ERROR = (
        155506,
        "retrieval kb_triple_index_build execution error, reason: {error_msg}",
    )
    RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR = (
        155507,
        "retrieval kb_triple_extraction process error, reason: {error_msg}",
    )
    RETRIEVAL_KB_DATABASE_CONFIG_INVALID = (155508, "retrieval kb_database_config is invalid, reason: {error_msg}")

    # KnowledgeBase Retrieval - Reranker 155600 - 155699
    RETRIEVAL_RERANKER_REQUEST_CALL_FAILED = (
        155600,
        "retrieval reranker_request call failed, reason: {error_msg}",
    )
    RETRIEVAL_RERANKER_UNREACHABLE_CALL_FAILED = (155601, "retrieval reranker call failed, reason: {error_msg}")
    RETRIEVAL_RERANKER_INPUT_INVALID = (155602, "retrieval reranker_input is invalid, reason: {error_msg}")

    # KnowledgeBase Retrieval - Query Rewriter 155603 - 155609
    RETRIEVAL_QUERY_REWRITER_PROMPT_NOT_FOUND = (
        155603,
        "retrieval query_rewriter prompt file not found, reason: {error_msg}",
    )
    RETRIEVAL_QUERY_REWRITER_OUTPUT_INVALID = (
        155604,
        "retrieval query_rewriter llm output is not valid JSON, reason: {error_msg}",
    )
    RETRIEVAL_QUERY_REWRITER_LLM_INVOKE_FAILED = (
        155605,
        "retrieval query_rewriter llm invoke failed, reason: {error_msg}",
    )
    RETRIEVAL_QUERY_REWRITER_INPUT_INVALID = (
        155606,
        "retrieval query_rewriter input is invalid, reason: {error_msg}",
    )

    # =========================
    # Memory Engine 158000 – 159999
    # =========================
    MEMORY_REGISTER_STORE_EXECUTION_ERROR = (158000, "failed to register {store_type} to memory engine, "
                                                     "reason: {error_msg}")
    MEMORY_SET_CONFIG_EXECUTION_ERROR = (158001, "failed to set {config_type} config, reason: {error_msg}")
    MEMORY_ADD_MEMORY_EXECUTION_ERROR = (158002, "failed to add {memory_type} memory, reason: {error_msg}")
    MEMORY_DELETE_MEMORY_EXECUTION_ERROR = (158003, "failed to delete {memory_type} memory, reason: {error_msg}")
    MEMORY_UPDATE_MEMORY_EXECUTION_ERROR = (158004, "failed to update {memory_type} memory, reason: {error_msg}")
    MEMORY_GET_MEMORY_EXECUTION_ERROR = (158005, "failed to get {memory_type} memory, reason: {error_msg}")
    MEMORY_STORE_INIT_FAILED = (158006, "failed to init {store_type}, reason: {error_msg}")
    MEMORY_CONNECT_STORE_EXECUTION_ERROR = (158007, "failed to connect {store_type}, reason: {error_msg}")
    MEMORY_STORE_VALIDATION_INVALID = (158008, "{store_type} validation failed, reason: {error_msg}")
    MEMORY_REGISTER_OPERATION_VALIDATION_INVALID = (
        158009,
        "failed to register operation for {entity_key}:{schema_version}, reason: {error_msg}"
    )
    MEMORY_MIGRATE_MEMORY_EXECUTION_ERROR = (158010, "failed to migrate memory, reason: {error_msg}")

    # Memory Engine - Common Utility 158200 - 158299
    MEMORY_GRAPH_LANGUAGE_INVALID = (158200, "graph memory language invalid: {error_msg}")
    MEMORY_GRAPH_EMBEDDING_CALL_FAILED = (158201, "graph memory embedding call failed, reason: {error_msg}")
    MEMORY_GRAPH_EMBED_MODEL_NOT_FOUND = (158202, "graph memory embedder not configured: {error_msg}")
    MEMORY_GRAPH_INVOKE_LLM_FAILED = (158203, "graph memory LLM invoke failed, reason: {error_msg}")
    MEMORY_GRAPH_PROMPT_FILES_MISSING = (158204, "graph memory prompt files not found in directory {prompt_dir}")

    # =========================
    # DevTools / AgentBuilder 140000 - 140099
    # =========================

    AGENT_BUILDER_RESOURCE_RETRIEVE_ERROR = (
        140031, "agent builder resource retrieve failed, error='{reason}'")
    AGENT_BUILDER_AGENT_TYPE_NOT_SUPPORTED = (
        140032, "agent builder agent_type is not supported, agent_type='{agent_type}', "
                "supported_types='{supported_types}'")

    # =========================
    # Foundation Tool 160000–169999
    # =========================

    # =========================
    # Optimization Toolchain 170000 - 179999
    # =========================

    # Optimization Toolchain - Prompt Self-optimization 170000 - 170999
    TOOLCHAIN_AGENT_PARAM_ERROR = (
        170000, "toolchain agent parameter error, reason: {error_msg}"
    )
    TOOLCHAIN_OPTIMIZER_BACKWARD_EXECUTION_ERROR = (
        170001, "toolchain optimizer_backword execution error, reason: {error_msg}"
    )
    TOOLCHAIN_OPTIMIZER_UPDATE_EXECUTION_ERROR = (
        170002, "toolchain optimizer_update execution error, reason: {error_msg}"
    )
    TOOLCHAIN_OPTIMIZER_PARAM_ERROR = (170003, "toolchain optimizer parameter error, reason: {error_msg}")
    TOOLCHAIN_EVALUATOR_EXECUTION_ERROR = (170004, "toolchain evaluator execution error, reason: {error_msg}")
    TOOLCHAIN_TRAINER_EXECUTION_ERROR = (170005, "toolchain trainer execution error, reason: {error_msg}")

    # Optimization Toolchain - End-to-end Performance Optimization 171000 - 171999

    # Optimization Toolchain - AgentRL 172000 - 172999
    AGENT_RL_PROXY_NOT_INITIALIZED = (
        172000, "agent_rl proxy has not been initialized, reason: {error_msg}")
    AGENT_RL_PROXY_SERVER_START_FAILED = (
        172001, "agent_rl proxy server failed to start, host='{host}', port='{port}'")

    # AgentRL - Executor 172010 - 172019
    AGENT_RL_EXECUTOR_NOT_INITIALIZED = (
        172010, "agent_rl executor is not configured, reason: {error_msg}")

    # AgentRL - Processor 172020 - 172029
    AGENT_RL_PROCESSOR_NOT_FOUND = (
        172020,
        "agent_rl {processor_type} processor not found, name='{name}', available='{available}'")

    # AgentRL - Dependency 172030 - 172039
    AGENT_RL_DEPENDENCY_INIT_FAILED = (
        172030, "agent_rl required dependency initialization failed, reason: {error_msg}")

    # AgentRL - Rollout 172040 - 172049
    AGENT_RL_ROLLOUT_BATCH_EXECUTION_ERROR = (
        172040, "agent_rl rollout batch execution error, reason: {error_msg}")

    # AgentRL - Trainer / Strategy 172050 - 172059
    AGENT_RL_STRATEGY_NOT_SUPPORTED = (
        172050, "agent_rl training strategy is not supported, strategy='{strategy}'")
    AGENT_RL_TRAINER_NOT_INITIALIZED = (
        172051, "agent_rl trainer not initialized, reason: {error_msg}")
    AGENT_RL_TASK_RUNNER_NOT_INITIALIZED = (
        172052, "agent_rl task runner not initialized, reason: {error_msg}")

    # AgentRL - Dataset / Batch 172060 - 172069
    AGENT_RL_VALIDATION_DATASET_INVALID = (
        172060, "agent_rl validation dataset is invalid, reason: {error_msg}")
    AGENT_RL_BATCH_DATA_TYPE_INVALID = (
        172061, "agent_rl batch data type is invalid, data_type='{data_type}'")

    # AgentRL - Reward 172070 - 172079
    AGENT_RL_REWARD_NAME_INVALID = (
        172070, "agent_rl reward name is invalid, reason: {error_msg}")
    AGENT_RL_REWARD_NOT_FOUND = (
        172071, "agent_rl reward function not found, name='{name}'")

    # Optimization Toolchain - Prompt Builder 173000 - 173999
    TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR = (
        173000, "toolchain meta_template execution error, reason: {error_msg}"
    )
    TOOLCHAIN_FEEDBACK_TEMPLATE_EXECUTION_ERROR = (
        173001, "toolchain feedback_template execution error, reason: {error_msg}"
    )
    TOOLCHAIN_BAD_CASE_TEMPLATE_EXECUTION_ERROR = (
        173002, "toolchain bad_case_template execution error, reason: {error_msg}"
    )

    # Optimization Toolchain - Task Memory (Middleware/Service) 174000 - 174025
    TOOLCHAIN_EVOLVING_MEMORY_RETRIEVE_EXECUTION_ERROR = (
        174000, "toolchain evolving memory retrieve execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_MEMORY_SUMMARIZE_EXECUTION_ERROR = (
        174001, "toolchain evolving memory summarize execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_MEMORY_ADD_EXECUTION_ERROR = (
        174002, "toolchain evolving memory add memory execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_MEMORY_FETCH_EXECUTION_ERROR = (
        174003, "toolchain evolving memory get playbook execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_MEMORY_CLEAR_EXECUTION_ERROR = (
        174004, "toolchain evolving memory clear playbook execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_MEMORY_CONFIG_INVALID = (
        174005, "toolchain evolving memory config is invalid, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_MEMORY_SERVICE_INIT_FAILED = (
        174006, "toolchain evolving memory service initialization failed, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_MEMORY_EMBEDDING_EXECUTION_ERROR = (
        174007, "toolchain evolving memory embedding execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_MEMORY_LLM_GENERATION_EXECUTION_ERROR = (
        174008, "toolchain evolving memory llm generation execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_MEMORY_DB_CONNECTOR_EXECUTION_ERROR = (
        174009, "toolchain evolving memory db connector execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_MEMORY_FILE_IO_EXECUTION_ERROR = (
        174010, "toolchain evolving memory file I/O execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_MEMORY_VECTOR_STORE_EXECUTION_ERROR = (
        174011, "toolchain evolving memory vector store execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_MEMORY_INJECTION_EXECUTION_ERROR = (
        174012, "toolchain evolving memory prompt injection execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_MEMORY_STATE_RESTORE_EXECUTION_ERROR = (
        174013, "toolchain evolving memory prompt state restore execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_MEMORY_INPUT_INVALID = (
        174014, "toolchain evolving memory input is invalid, reason: {error_msg}"
    )

    # Optimization Toolchain - Tool Self-optimization 174025 - 174049
    TOOLCHAIN_EVOLVING_TOOL_CALL_CONFIG_ERROR = (
        174025, "toolchain optimizer tool_call config error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_TOOL_CALL_PARAM_ERROR = (
        174026, "toolchain optimizer tool_call parameter error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_TOOL_CALL_RUNTIME_ERROR = (
        174027, "toolchain optimizer tool_call runtime error, reason: {error_msg}"
    )

    TOOLCHAIN_EVOLVING_TOOL_CALL_EXAMPLE_STAGE_EXECUTION_ERROR = (
        174028, "toolchain optimizer tool_call example_stage execution error, reason: {error_msg}"
    )

    TOOLCHAIN_EVOLVING_TOOL_CALL_BEAM_SEARCH_EXECUTION_ERROR = (
        174029, "toolchain optimizer tool_call beam_search execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_TOOL_CALL_EVALUATOR_EXECUTION_ERROR = (
        174030, "toolchain optimizer tool_call evaluator execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_TOOL_CALL_LLM_CALL_EXECUTION_ERROR = (
        174031, "toolchain optimizer tool_call llm_call execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_TOOL_CALL_REVIEWER_EXECUTION_ERROR = (
        174032, "toolchain optimizer tool_call reviewer execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_TOOL_CALL_SCHEMA_EXTRACT_EXECUTION_ERROR = (
        174033, "toolchain optimizer tool_call schema_extract execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_TOOL_CALL_OUTPUT_PARSE_ERROR = (
        174034, "toolchain optimizer tool_call output parse error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_TOOL_CALL_LOGGING_EXECUTION_ERROR = (
        174035, "toolchain optimizer tool_call logging execution error, reason: {error_msg}"
    )
    TOOLCHAIN_EVOLVING_TOOL_CALL_RESULT_PERSIST_EXECUTION_ERROR = (
        174036, "toolchain optimizer tool_call result persist execution error, reason: {error_msg}"
    )

    # =========================
    # Foundation 180000 – 189999
    # =========================

    # Foundation - Prompt Template 180000 - 180999
    PROMPT_ASSEMBLER_VARIABLE_INIT_FAILED = (180000,
                                             "prompt assembler_variable initialization failed, reason: {error_msg}")
    PROMPT_ASSEMBLER_TEMPLATE_PARAM_ERROR = (
        180001, "prompt assembler_template parameter error, reason: {error_msg}")
    PROMPT_TEMPLATE_RUNTIME_ERROR = (180002, "prompt template runtime error, reason: {error_msg}")
    PROMPT_TEMPLATE_NOT_FOUND = (180003, "prompt template not found, reason: {error_msg}")
    PROMPT_TEMPLATE_INVALID = (180004, "prompt template is invalid, reason: {error_msg}")

    # Foundation - Model API 181000 - 181999
    MODEL_PROVIDER_INVALID = (181000, "model provider is invalid, reason: {error_msg}")
    MODEL_CALL_FAILED = (181001, "model call failed, reason: {error_msg}")
    MODEL_SERVICE_CONFIG_ERROR = (181002, "model service config error, reason: {error_msg}")
    MODEL_CONFIG_ERROR = (181003, "model config error, reason: {error_msg}")
    MODEL_INVOKE_PARAM_ERROR = (181004, "model invoke parameter error, reason: {error_msg}")
    MODEL_CLIENT_CONFIG_INVALID = (181005, "model client_config is invalid, reason: {error_msg}")

    # 2. Foundation - Tool Definition and Execution 182000 - 182999
    # 0. basic tool
    TOOP_CARD_INVALID = (182000, "card is invalid, card={card}, error='{reason}'")
    TOOL_STREAM_NOT_SUPPORTED = (182010, "stream is not support, card={card}")
    TOOL_INVOKE_NOT_SUPPORTED = (182011, "invoke is not support, card={card}")
    TOOL_EXECUTION_ERROR = (182012, "tool execution error, too card={card}, reason={reason}")

    # 1. restuful api 182100 - 182199
    TOOL_RESTFUL_API_CARD_CONFIG_INVALID = (182100, "config failed, {reason}")
    TOOL_RESTFUL_API_EXECUTION_TIMEOUT = (
        182101,
        "execute {method} failed, request is timeout, timeout={timeout}s, card=[{card}]")
    TOOL_RESTFUL_API_RESPONSE_SIZE_EXCEED_LIMIT = (
        182102,
        "execute {method} failed, response is too big, max_size={max_length}b, actual={actual_length}b, card=[{card}]")
    TOOL_RESTFUL_API_RESPONSE_ERROR = (
        182103, "execute {method} failed, response error, code={code}, error='{reason}'")
    TOOL_RESTFUL_API_EXECUTION_ERROR = (
        182104, "RestfulApi execute {method} failed, error='{reason}', card=[{card}]")
    TOOL_RESTFUL_API_RESPONSE_PROCESS_ERROR = (
        182105, "RestfulApi parse response failed, error='{reason}', card=[{card}]")
    # 2. local function 182200 - 182299
    TOOL_LOCAL_FUNCTION_FUNC_NOT_SUPPORTED = (
        182200, "func is not supported, card={card}")
    TOOL_LOCAL_FUNCTION_EXECUTION_ERROR = (
        182205, "execute {method} failed, error='{reason}', card={card}")

    # 3. mcp tool 182300 - 182399
    TOOL_MCP_CLIENT_NOT_SUPPORTED = (182300, "mcp client is not supported, card={card}")
    TOOL_MCP_EXECUTION_ERROR = (182301, "execute {method} failed, error='{reason}', card={card}")

    # 4. openapi tool 182400 - 182499
    TOOL_OPENAPI_CLIENT_EXECUTION_ERROR = (182400, "openapi client execute error, error='{reason}'")

    # 5. deepagents tool 182500 - 182699
    TOOL_TODOS_LOAD_FAILED = (182500, "todo tool loads failed, error='{reason}'")
    TOOL_TODOS_SAVE_FAILED = (182501, "todo tool saves failed, error='{reason}'")
    TOOL_TODOS_CLEAR_FAILED = (182502, "todo tool clears failed, error='{reason}'")
    TOOL_TODOS_VALIDATION_INVALID = (182503, "todo tool validation invalid, error='{reason}'")
    TOOL_TODOS_INVOKE_FAILED = (182504, "todo tool invoke failed, error='{reason}'")
    TOOL_TASK_TOOL_INVOKED = (182505, "task tool invoked, error='{reason}'")

    TOOL_WEB_SEARCH_ENGINE_ERROR = (182506, "web search engine error, engine='{engine}', reason='{reason}'")
    TOOL_WEB_SEARCH_ALL_ENGINES_FAILED = (182507, "all web search engines failed, errors='{errors}'")
    TOOL_WEB_API_KEY_NOT_SET = (182508, "web tool api key is not set, key_name='{key_name}'")

    # Foundation - Logger 183000 - 183999
    COMMON_LOG_PATH_INVALID = (183000, "common log_path is invalid, reason: {error_msg}")
    COMMON_LOG_PATH_INIT_FAILED = (183001, "common log_path initialization failed, reason: {error_msg}")
    COMMON_LOG_CONFIG_PROCESS_ERROR = (183002, "common log_config process error, reason: {error_msg}")
    COMMON_LOG_CONFIG_INVALID = (183003, "common log_config is invalid, reason: {error_msg}")
    COMMON_LOG_EXECUTION_RUNTIME_ERROR = (183004, "common log_execution runtime error, reason: {error_msg}")

    #Task Manager 184000 - 184099
    COMMON_TASK_CONFIG_ERROR = (184000, "common coroutine task config error, reason: {error_msg}")
    COMMON_TASK_NOT_FOUND = (184001, "common coroutine task not found, reason: {error_msg}")
    # Foundation - Support Mcp Tool 185000 - 185999

    # Foundation - Store supporting 186000 - 186100
    STORE_VECTOR_SCHEMA_INVALID = (186000, "store vector_schema is invalid, reason: {error_msg}")
    STORE_VECTOR_DOC_INVALID = (186001, "store vector_doc is invalid, reason: {error_msg}")
    STORE_VECTOR_COLLECTION_NOT_FOUND = (186002, "store vector_collection not found, collection_name={collection_name}")
    STORE_GRAPH_PARAM_INVALID = (186003, "store graph_param invalid, reason: {error_msg}")
    STORE_GRAPH_BACKEND_NAME_INVALID = (186004, "store graph_backend name invalid, reason: {error_msg}")
    STORE_GRAPH_BACKEND_ALREADY_EXISTS = (186005, "store graph_backend exists, name={name}, existing={existing}")
    STORE_GRAPH_PROTOCOL_NOT_IMPLEMENTED = (186006, "store graph_protocol not implemented, reason: {error_msg}")
    STORE_GRAPH_BACKEND_NOT_FOUND = (186007, "store graph_backend not found, please register it first, name={name}")
    STORE_GRAPH_FACTORY_NOT_INSTANTIABLE = (186008, "store graph_factory must not be instantiated, class={class_name}")
    STORE_GRAPH_COLLECTION_NOT_SUPPORTED = (186009, "store graph_collection not supported, collection={collection}")

    # Foundation - Common Utility 188000 - 188999
    COMMON_SSL_CONTEXT_INIT_FAILED = (188000, "common ssl_context initialization failed, reason: {error_msg}")
    COMMON_USER_CONFIG_PROCESS_ERROR = (188001, "common user_config process error, reason: {error_msg}")
    COMMON_JSON_INPUT_PROCESS_ERROR = (188002, "common json_input process error, reason: {error_msg}")
    COMMON_JSON_EXECUTION_PROCESS_ERROR = (188003, "common json_execution process error, reason: {error_msg}")
    COMMON_URL_INPUT_INVALID = (188004, "common url_input is invalid, reason: {error_msg}")
    COMMON_SSL_CERT_INVALID = (188005, "common ssl_cert is invalid, reason: {error_msg}")

    # Foundation - Schema 189000 - 189999
    SCHEMA_VALIDATE_INVALID = (189001, "validate data with schema failed, error='{reason}', data={data}")
    SCHEMA_FORMAT_INVALID = (189002, "format data with schema failed, error='{reason}', data={data}")

    # =========================
    # Security / Guardrail 190000 - 190999
    # =========================

    GUARDRAIL_BLOCKED = (
        190000,
        "guardrail blocked: risk_type='{risk_type}', risk_level='{risk_level}', event='{event}'")

    # =========================
    # SysOperation 199000–199999
    # =========================

    SYS_OPERATION_MANAGER_PROCESS_ERROR = (
        199001,
        "sys operation manager process error, process: {process}, reason: {error_msg}")
    SYS_OPERATION_CARD_PARAM_ERROR = (
        199002,
        "sys operation card param error, reason: {error_msg}")
    SYS_OPERATION_FS_EXECUTION_ERROR = (
        199003,
        "file system operation execution error, execution: {execution}, reason: {error_msg}")
    SYS_OPERATION_SHELL_EXECUTION_ERROR = (
        199004,
        "shell operation execution error, execution: {execution}, reason: {error_msg}")
    SYS_OPERATION_CODE_EXECUTION_ERROR = (
        199005,
        "code operation execution error, execution: {execution}, reason: {error_msg}")
    SYS_OPERATION_REGISTRY_ERROR = (
        199006,
        "sys operation registry error, process: {process}, reason: {error_msg}")

    def __init__(self, code: int, msg: str):
        """Validate and initialize enum member values.

        Args:
            code: integer status code
            msg: error message template (supports str.format placeholders)
        """
        if not isinstance(code, int):
            raise TypeError(f"StatusCode code must be int, got {type(code)!r} for {self.name}")
        if not isinstance(msg, str):
            raise TypeError(f"StatusCode errmsg must be str, got {type(msg)!r} for {self.name}")
        self._code = code
        self._msg = msg

    @property
    def code(self) -> int:
        """Return the integer error code."""
        return self._code

    @property
    def errmsg(self) -> str:
        """Return the error message template (unformatted)."""
        return self._msg
