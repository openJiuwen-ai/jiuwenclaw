# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.core.memory.process.extract.common import ExtractMemoryParams
from openjiuwen.core.memory.process.extract.long_term_memory_extractor import LongTermMemoryExtractor
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType, BaseMemoryUnit, \
    VariableUnit, FragmentMemoryUnit, SummaryUnit
from openjiuwen.core.memory.manage.mem_model.data_id_manager import DataIdManager
from openjiuwen.core.memory.process.extract.memory_analyzer import MemoryAnalyzer, VariableResult
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType

category_to_class = {
    "user_profile": MemoryType.USER_PROFILE,
    "semantic_memory": MemoryType.SEMANTIC_MEMORY,
    "episodic_memory": MemoryType.EPISODIC_MEMORY,
}


class Generator:
    def __init__(self, data_id_generator: DataIdManager):
        self.data_id_generator = data_id_generator

    async def gen_all_memory(self, **kwargs) -> dict[str, list[BaseMemoryUnit]]:
        """Generate all memory units based on input"""
        messages = kwargs.get("messages")
        config = kwargs.get("config")
        model = kwargs.get("base_chat_model")
        user_id = kwargs.get("user_id")
        scope_id = kwargs.get("scope_id")
        history_messages = kwargs.get("history_messages")
        forbidden_variables = kwargs.get("forbidden_variables")
        message_mem_id = kwargs.get("message_mem_id")
        timestamp = kwargs.get("timestamp")
        summary_max_token = kwargs.get("summary_max_token")
        if not all([messages, config, user_id, scope_id, model]):
            memory_logger.error(
                "Messages, config, user_id, scope_id, model are required parameters",
                event_type=LogEventType.MEMORY_PROCESS,
                user_id=user_id,
                scope_id=scope_id
            )
            return {}

        extract_memory_params = ExtractMemoryParams(
            user_id=user_id,
            scope_id=scope_id,
            messages=messages,
            history_messages=history_messages,
            base_chat_model=model
        )

        all_memory_results = {}
        memory_analyze_res = await MemoryAnalyzer.analyze(
            messages=messages,
            history_messages=history_messages,
            base_chat_model=model,
            memory_config=config,
            summary_max_token=summary_max_token,
            forbidden_variables=forbidden_variables
        )
        variable_units = self._process_extracted_data(
            variable_results=memory_analyze_res.variables,
        )
        for unit in variable_units:
            mem_type = unit.mem_type.value
            if mem_type not in all_memory_results:
                all_memory_results[mem_type] = []
            all_memory_results[mem_type].append(unit)

        if not config.enable_long_term_mem:
            memory_logger.info(
                "Not enable long term memory",
                event_type=LogEventType.MEMORY_PROCESS,
                user_id=user_id,
                scope_id=scope_id,
            )
            return all_memory_results

        if config.enable_summary_memory:
            summary_unit = await self._process_summary_data(
                user_id=user_id,
                message_mem_id=message_mem_id,
                summary=memory_analyze_res.summary,
                timestamp=timestamp
            )
            summary_type = summary_unit.mem_type.value
            if summary_type not in all_memory_results:
                all_memory_results[summary_type] = []
            all_memory_results[summary_type].append(summary_unit)

        if not memory_analyze_res.has_key_information:
            return all_memory_results
        fragment_enable = {
            MemoryType.USER_PROFILE.value: config.enable_user_profile,
            MemoryType.SEMANTIC_MEMORY.value: config.enable_semantic_memory,
            MemoryType.EPISODIC_MEMORY.value: config.enable_episodic_memory,
        }

        try:
            merged_units = await self._categories_to_memory_unit(
                extract_memory_paras=extract_memory_params,
                message_mem_id=message_mem_id,
                timestamp=timestamp
            )
        except AttributeError as e:
            memory_logger.debug(
                "Get conflict info has attribute exception",
                event_type=LogEventType.MEMORY_PROCESS,
                user_id=user_id,
                scope_id=scope_id,
                exception=str(e)
            )
            return all_memory_results
        except ValueError as e:
            memory_logger.warning(
                "Get conflict info has value exception",
                event_type=LogEventType.MEMORY_PROCESS,
                user_id=user_id,
                scope_id=scope_id,
                exception=str(e)
            )
            return all_memory_results
        except BaseException as e:
            memory_logger.warning(
                "Get conflict info has exception",
                event_type=LogEventType.MEMORY_PROCESS,
                user_id=user_id,
                scope_id=scope_id,
                exception=str(e)
            )
            return all_memory_results
        for unit in merged_units:
            mem_type = unit.mem_type.value
            if mem_type not in all_memory_results:
                all_memory_results[mem_type] = []
            if fragment_enable.get(mem_type, False):
                all_memory_results[mem_type].append(unit)
        memory_logger.info(
            "Memory units generated successfully",
            event_type=LogEventType.MEMORY_PROCESS,
            user_id=user_id,
            scope_id=scope_id,
            metadata={"all_memory_result": all_memory_results}
        )
        return all_memory_results

    async def _categories_to_memory_unit(
        self,
        extract_memory_paras: ExtractMemoryParams,
        message_mem_id: str,
        timestamp: str
    ) -> list[BaseMemoryUnit]:
        memory_units = []
        memory_dict = await LongTermMemoryExtractor.extract_long_term_memory(
            extract_memory_paras=extract_memory_paras,
            timestamp=timestamp,
        )
        memory_units.extend(await self._get_fragment_memory_unit(
            user_id=extract_memory_paras.user_id,
            message_mem_id=message_mem_id,
            memory_dict=memory_dict,
            timestamp=timestamp,
        ))
        return memory_units

    @staticmethod
    def _process_extracted_data(variable_results: list[VariableResult]) -> list[VariableUnit]:
        variable_units = []
        for tmp_data in variable_results:
            if not tmp_data.variable_value:
                continue
            variable_units.append(VariableUnit(
                variable_name=tmp_data.variable_key,
                variable_mem=tmp_data.variable_value
            ))
        return variable_units

    async def _process_summary_data(
            self,
            user_id: str,
            message_mem_id: str,
            summary: str,
            timestamp: str,
    ) -> SummaryUnit:
        mem_id = str(await self.data_id_generator.generate_next_id(user_id=user_id))
        return SummaryUnit(
            mem_id=mem_id,
            summary=summary,
            message_mem_id=message_mem_id,
            timestamp=timestamp,
        )

    async def _get_fragment_memory_unit(
            self,
            user_id: str,
            message_mem_id: str,
            memory_dict: dict,
            timestamp: str
    ) -> list[FragmentMemoryUnit]:
        """Generate fragment memory unit based on input"""
        fragment_mem_units = []
        for fragment_type, fragment_memories in memory_dict.items():
            mem_type = category_to_class.get(fragment_type, None)
            if not mem_type:
                continue
            for mem_content in fragment_memories:
                if not isinstance(mem_content, str):
                    content = mem_content.get("content")
                    if content:
                        mem_content = content
                    else:
                        mem_content = str(mem_content)
                mem_id = str(await self.data_id_generator.generate_next_id(user_id=user_id))
                fragment_mem_units.append(FragmentMemoryUnit(
                    mem_type=mem_type,
                    content=mem_content,
                    message_mem_id=message_mem_id,
                    timestamp=timestamp,
                    mem_id=mem_id,
                ))
        return fragment_mem_units
