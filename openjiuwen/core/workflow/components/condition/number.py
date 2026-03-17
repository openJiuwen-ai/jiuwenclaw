# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Union

from openjiuwen.core.workflow.components.condition.condition import Condition
from openjiuwen.core.session import BaseSession
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.common.constants.constant import INDEX


class NumberCondition(Condition):
    def __init__(self, limit: Union[str, int]):
        super().__init__(limit)
        self._limit = limit

    def invoke(self, inputs: Input, session: BaseSession) -> Output:
        current_idx = session.state().get(INDEX)
        limit_num = inputs
        return current_idx < limit_num


class NumberConditionInSession(Condition):
    def __init__(self, limit: int):
        super().__init__()
        self._limit = limit

    def invoke(self, inputs: Input, session: BaseSession) -> Output:
        current_idx = session.state().get(INDEX)
        limit_num = self._limit
        if limit_num is None:
            raise ValueError("loop_number variable not found or is None")
            
        return current_idx < limit_num
