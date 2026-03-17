# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Tuple, Optional

from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.runner.resources_manager.thread_safe_dict import ThreadSafeDict


class PromptMgr:
    def __init__(self) -> None:
        self._repo: ThreadSafeDict[str, PromptTemplate] = ThreadSafeDict()

    def add_prompt(self, template_id: str, template: PromptTemplate) -> None:
        if template_id is None:
            raise ValueError('template_id is invalid, can not be None')
        if template is None:
            raise ValueError('template is invalid, can not be None')
        self._repo[template_id] = template

    def add_prompts(self, templates: List[Tuple[str, PromptTemplate]]) -> None:
        if templates is None:
            return
        for template_id, template in templates:
            self.add_prompt(template_id, template)

    def remove_prompt(self, template_id: str) -> Optional[PromptTemplate]:
        return self._repo.pop(template_id, None)

    def get_prompt(self, template_id: str) -> Optional[PromptTemplate]:
        if template_id is None:
            raise ValueError('template_id is invalid, can not be None')
        return self._repo.get(template_id)
