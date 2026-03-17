# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any

from openjiuwen.core.workflow.components.flow.loop.callback.loop_callback import LoopCallback
from openjiuwen.core.session import BaseSession, NESTED_PATH_SPLIT, is_ref_path, extract_origin_key
from openjiuwen.core.graph.executable import Output


class OutputCallback(LoopCallback):
    def __init__(self, outputs_format: dict[str, Any], round_result_root: str = None,
                 result_root: str = None):
        self._outputs_format = outputs_format
        self._result_root = result_root
        self._round_result_root = round_result_root if round_result_root else "round"

    def first_in_loop(self, session: BaseSession) -> Output:
        _results: list[Any] = []
        session.state().update({self._round_result_root: _results})
        return None

    def _generate_output(self, session: BaseSession, results: list[Any], root: list[str], output_format: Any):
        if isinstance(output_format, dict):
            output = {}
            for key, value in output_format.items():
                path = root.copy()
                path.append(key)
                output[key] = self._generate_output(session, results, path, value)
            return output
        if isinstance(output_format, str) and is_ref_path(output_format):
            ref_str = extract_origin_key(output_format)
            path = ref_str.split(NESTED_PATH_SPLIT)
            if path[0] == session.node_id():
                if len(results) == 0:
                    return None
                data = results[-1]
                for key in root:
                    data = data.get(key)
                return data

        output = []
        for result in results:
            data = result
            for key in root:
                data = data.get(key)
            output.append(data)
        return output

    def out_loop(self, session: BaseSession) -> Output:
        results: list[Any] = session.state().get(self._round_result_root)
        return self._generate_output(session, results, [], self._outputs_format)

    def start_round(self, session: BaseSession) -> Output:
        return None

    def end_round(self, session: BaseSession, loop_times: int) -> Output:
        results: list[Any] = session.state().get(self._round_result_root)
        if not isinstance(results, list):
            raise ValueError("error results in round process")
        if len(results) >= loop_times:
            return None
        results.append(session.state().get_inputs(self._outputs_format))
        session.state().update({self._round_result_root: results})
        return None
