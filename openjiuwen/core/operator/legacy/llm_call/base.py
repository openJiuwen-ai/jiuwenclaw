# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Dict, Any, Optional, List, Callable, AsyncIterator

from openjiuwen.core.session.agent import Session
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.llm import BaseMessage, SystemMessage, Model
from openjiuwen.core.foundation.tool import ToolInfo

DEFAULT_USER_PROMPT: str = "{{query}}"


class LLMCall:
    def __init__(self,
                 model_name: str,
                 llm: Model,
                 system_prompt: str | List[BaseMessage] | List[Dict],
                 user_prompt: str | List[BaseMessage] | List[Dict],
                 freeze_system_prompt: bool = False,
                 freeze_user_prompt: bool = True,
                 llm_call_id: str = "llm_call",
                 ) -> None:
        self._llm = llm
        self._model_name = model_name
        self._system_prompt = PromptTemplate(content=system_prompt)
        self._user_prompt = PromptTemplate(content=user_prompt or DEFAULT_USER_PROMPT)
        self._freeze_system_prompt = freeze_system_prompt
        self._freeze_user_prompt = freeze_user_prompt
        self._optimizer_callback: Optional[Callable] = None
        self._llm_call_id = llm_call_id

    async def invoke(self,
                     inputs: Dict[str, Any],
                     session: Session,
                     history: Optional[List[BaseMessage]] = None,
                     tools: Optional[List[ToolInfo]] = None,
                     ) -> BaseMessage:
        messages = self._format_llm_input(inputs, history)
        response = await self._llm.invoke(model=self._model_name, messages=messages, tools=tools)
        if self._optimizer_callback is not None:
            await self._optimizer_callback(self._llm_call_id, inputs, response, session)
        return response

    async def stream(self,
                     inputs: Dict[str, Any],
                     session: Session,
                     history: Optional[List[BaseMessage]] = None,
                     tools: Optional[List[ToolInfo]] = None,
                     ) -> AsyncIterator:
        messages = self._format_llm_input(inputs, history)
        message_chunks = []
        async for chunk in self._llm.stream(model=self._model_name, messages=messages, tools=tools):
            message_chunks.append(chunk.content if hasattr(chunk, "content") else str(chunk))
            yield chunk
        response = "".join(message_chunks)
        if self._optimizer_callback is not None:
            await self._optimizer_callback(self._llm_call_id, inputs, response, session)

    def get_optimizer_callback(self) -> Optional[Callable]:
        return self._optimizer_callback

    def set_optimizer_callback(self, callback: Optional[Callable]) -> None:
        self._optimizer_callback = callback

    def get_system_prompt(self) -> PromptTemplate:
        return self._system_prompt

    def get_user_prompt(self) -> PromptTemplate:
        return self._user_prompt

    def update_system_prompt(self, system_prompt: str | List[BaseMessage] | List[Dict]) -> None:
        if not self._freeze_system_prompt:
            self._system_prompt = PromptTemplate(content=system_prompt)

    def update_user_prompt(self, user_prompt: str | List[BaseMessage] | List[Dict]) -> None:
        if not self._freeze_user_prompt:
            self._user_prompt = PromptTemplate(content=user_prompt)

    def set_freeze_system_prompt(self, switch: bool) -> None:
        self._freeze_system_prompt = switch

    def set_freeze_user_prompt(self, switch: bool) -> None:
        self._freeze_user_prompt = switch

    def get_freeze_system_prompt(self) -> bool:
        return self._freeze_system_prompt

    def get_freeze_user_prompt(self) -> bool:
        return self._freeze_user_prompt

    def _format_llm_input(self,
                          inputs: Dict[str, Any],
                          history: Optional[List[BaseMessage]] = None,
                          ) -> List[BaseMessage]:
        system_messages = [
            SystemMessage(
                content=msg.content,
            ) for msg in self._system_prompt.format(inputs).to_messages()
        ]
        user_messages = self._user_prompt.format(inputs).to_messages()
        history_messages = history if history is not None else []
        return [*system_messages, *history_messages, *user_messages]