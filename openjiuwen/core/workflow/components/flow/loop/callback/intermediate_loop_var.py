# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Union, Any

from openjiuwen.core.workflow.components.flow.loop.callback.loop_callback import LoopCallback
from openjiuwen.core.session import BaseSession
from openjiuwen.core.graph.executable import Output


class IntermediateLoopVarCallback(LoopCallback):
    def __init__(self, intermediate_loop_var: dict[str, Union[str, Any]],
                 intermediate_loop_var_root: str = ""):
        self.intermediate_loop_var = intermediate_loop_var
        self.intermediate_loop_var_root = intermediate_loop_var_root

    def first_in_loop(self, session: BaseSession) -> Output:
        local_vars = session.state().get_inputs(self.intermediate_loop_var)
        if self.intermediate_loop_var_root:
            local_vars = {self.intermediate_loop_var_root: local_vars}
        return local_vars

    def out_loop(self, session: BaseSession) -> Output:
        return None

    def start_round(self, session: BaseSession) -> Output:
        return None

    def end_round(self, session: BaseSession, loop_times: int) -> Output:
        return None
