# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import random
from typing import List, Tuple, Dict, Optional
from concurrent.futures import ThreadPoolExecutor
import copy

from tqdm import tqdm

from openjiuwen.dev_tools.tune.utils import TuneUtils
from openjiuwen.core.single_agent.legacy import LegacyBaseAgent as BaseAgent
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.operator.legacy.llm_call.base import LLMCall
from openjiuwen.dev_tools.tune.base import EvaluatedCase, TuneConstant, Case
from openjiuwen.dev_tools.tune.dataset.case_loader import CaseLoader
from openjiuwen.dev_tools.tune.evaluator.evaluator import BaseEvaluator
from openjiuwen.dev_tools.tune.optimizer.base import BaseOptimizer, TextualParameter
from openjiuwen.dev_tools.tune.trainer.base import Progress, Callbacks

DEFAULT_CANDIDATES_SAMPLE_NUM: int = 6


class Trainer:
    def __init__(self,
                 optimizer: BaseOptimizer,
                 evaluator: BaseEvaluator,
                 **kwargs
                 ):
        self._optimizer = optimizer
        self._evaluator = evaluator

        self._num_parallel = kwargs.get("num_parallel", TuneConstant.DEFAULT_PARALLEL_NUM)
        TuneUtils.validate_digital_parameter(self._num_parallel, "num_parallel",
                                             TuneConstant.MIN_PARALLEL_NUM, TuneConstant.MAX_PARALLEL_NUM)
        self._early_stop_score = kwargs.get("early_stop_score", TuneConstant.DEFAULT_EARLY_STOP_SCORE)
        TuneUtils.validate_digital_parameter(self._early_stop_score, "early_stop_score",
                                             0.0, 1.0)
        self._callbacks = Callbacks()

    def train(self,
              agent: BaseAgent,
              train_cases: CaseLoader,
              val_cases: Optional[CaseLoader] = None,
              **kwargs
              ) -> Optional[BaseAgent]:
        if not self._check_trainable(agent):
            raise build_error(
                StatusCode.TOOLCHAIN_TRAINER_EXECUTION_ERROR,
                error_msg=f"trainer only support current Agent right now"
            )
        progress: Progress = self._pre_train(agent, **kwargs)
        if not val_cases:
            val_cases = train_cases
        progress.current_epoch_score, cur_epoch_evaluated_cases = self.evaluate(agent, val_cases)
        progress.best_score = progress.current_epoch_score
        self._callbacks.on_train_begin(agent, progress, cur_epoch_evaluated_cases)
        if progress.best_score >= self._early_stop_score:
            logger.info(f"val set score {progress.best_score} already exceed target score {self._early_stop_score}, "
                        f"skip optimization")
            self._callbacks.on_train_end(agent, progress, cur_epoch_evaluated_cases)
            return agent
        logger.info(f"val set baseline score: {progress.current_epoch_score}")
        parameter_searcher = ParameterSearcher(self, case_loader=val_cases)
        score = 0.0
        for _ in progress.run_epoch():
            # get trace of execution
            self._callbacks.on_train_epoch_begin(agent, progress)
            with self._optimizer:
                score, cur_evaluated_cases = self.evaluate(agent, train_cases)
                logger.info(f"train epoch {progress.current_epoch}, train set score: {score}")
            cur_parameters = copy.deepcopy(agent.get_llm_calls())

            best_batch_parameters = cur_parameters
            for _ in progress.run_batch():
                with self._optimizer as optimizer:
                    optimizer.backward(cur_evaluated_cases)
                    optimizer.update()
                score, cur_batch_parameters, cur_epoch_evaluated_cases, progress.current_epoch_score = (
                    parameter_searcher.search_best(
                    agent=agent,
                    base_score=progress.best_score,
                    base_parameters=cur_parameters,
                    parameters=[agent.get_llm_calls()],
                ))
                if score > progress.best_batch_score:
                    progress.best_batch_score = score
                    best_batch_parameters = cur_batch_parameters
            logger.info(f"train epoch {progress.current_epoch}, val set score: {score}")
            if progress.best_batch_score > progress.best_score:
                progress.best_score = progress.best_batch_score
                self._update_agent(agent, best_batch_parameters)
                self._callbacks.on_train_epoch_end(agent, progress, cur_epoch_evaluated_cases)
            else:
                self._callbacks.on_train_epoch_end(agent, progress, cur_epoch_evaluated_cases)
                self._update_agent(agent, cur_parameters)
            if progress.best_score >= self._early_stop_score:
                break
        self._callbacks.on_train_end(agent, progress, cur_epoch_evaluated_cases)

        return agent

    def evaluate(self,
                 agent: BaseAgent,
                 cases: CaseLoader,
                 ) -> Tuple[float, List[EvaluatedCase]]:
        if not cases.get_cases():
            return 0.0, []
        predicts = self.predict(agent, cases)
        evaluated_cases = self._evaluator.batch_evaluate(cases.get_cases(), predicts, num_parallel=self._num_parallel)
        score = sum(case.score for case in evaluated_cases) / len(evaluated_cases) \
            if evaluated_cases else 0.0
        return score, evaluated_cases

    def predict(self,
                agent: BaseAgent,
                cases: CaseLoader
                ) -> List[Dict]:
        def forward(case: Case) -> Dict:
            try:
                result = asyncio.run(agent.invoke({**case.inputs, "conversation_id": case.case_id}))
            except Exception as e:
                return dict(error=f"Get wrong result due to {str(e)}")
            return result
        num_workers = min(self._num_parallel, cases.size())
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            predicts = executor.map(
                forward, cases.get_cases()
            )
            return list(tqdm(predicts, desc="forward", total=cases.size()))

    def set_callbacks(self, callbacks: Callbacks):
        if not isinstance(callbacks, Callbacks):
            logger.warning(f"callbacks should be a Callbacks object, got {type(callbacks)}")
            return
        self._callbacks = callbacks

    def _pre_train(self, agent: BaseAgent, **kwargs) -> Progress:
        max_epoch = kwargs.get('num_iterations', TuneConstant.DEFAULT_ITERATION_NUM)
        TuneUtils.validate_digital_parameter(max_epoch, "num_iterations",
                                             TuneConstant.MIN_ITERATION_NUM, TuneConstant.MAX_ITERATION_NUM)
        progress = Progress(
            max_epoch=max_epoch
        )

        self._optimizer.bind_parameter(agent.get_llm_calls())
        return progress

    @staticmethod
    def _update_agent(agent: BaseAgent, parameters: Optional[Dict[str, TextualParameter | LLMCall]] = None):
        if parameters is None:
            return
        agent_parameters = agent.get_llm_calls()
        for name, llm_call in agent_parameters.items():
            param = parameters.get(name)
            if not param:
                continue
            if isinstance(param, TextualParameter):
                llm_call.update_system_prompt(param.llm_call.get_system_prompt().content)
                llm_call.update_user_prompt(param.llm_call.get_user_prompt().content)
            elif isinstance(param, LLMCall):
                llm_call.update_system_prompt(param.get_system_prompt().content)
                llm_call.update_user_prompt(param.get_user_prompt().content)

    def update_agent(self, agent: BaseAgent, parameters: Optional[Dict[str, TextualParameter | LLMCall]] = None):
        return self._update_agent(agent, parameters)

    @staticmethod
    def _check_trainable(agent: BaseAgent) -> bool:
        method = getattr(type(agent), "get_llm_calls")
        if not method:
            return False
        owner = method.__qualname__.split('.')[0]
        return owner != type(agent).__bases__[0].__name__


class ParameterSearcher:
    def __init__(self, trainer: Trainer, case_loader: CaseLoader):
        self._trainer = trainer
        self._case_loader = case_loader

    def search_best(self,
                    agent: BaseAgent,
                    base_score: float,
                    base_parameters: Dict[str, LLMCall],
                    parameters: List[Dict[str, LLMCall]]):
        candidates = self.generate_candidates([base_parameters, *parameters])
        candidates.pop(0)
        candidates = random.sample(candidates,
                                   k=min(DEFAULT_CANDIDATES_SAMPLE_NUM, len(candidates)))
        best_score = base_score
        best_parameters = base_parameters
        best_cases = None
        logger.info(f"start searching best parameter group from {len(candidates)} candidates, "
                    f"current epoch baseline score: {best_score}")
        for i, candidate in enumerate(candidates):
            self._trainer.update_agent(agent, candidate)
            score, evaluated_cases = self._trainer.evaluate(agent, self._case_loader)
            logger.info(f"finish evaluating candidate {i}, score {score}")
            if score > best_score:
                best_score = score
                best_parameters = candidate
                best_cases = evaluated_cases
            if best_cases is None:
                best_cases = evaluated_cases
        return best_score, best_parameters, best_cases, score

    @staticmethod
    def generate_candidates(parameters: List[Dict[str, LLMCall]]) -> List[Dict[str, LLMCall]]:
        n_params = len(parameters[0])
        n_candidates = len(parameters)
        node_names = list(parameters[0].keys())
        all_candidates = []

        def generate_candidates_recursively(i_param: int, candidate: Dict[str, LLMCall]):
            if i_param == n_params:
                try:
                    all_candidates.append(copy.deepcopy(candidate))
                except Exception as e:
                    for candidate in candidate.values():
                        logger.error(f"Candidate type is {type(candidate)} content is {candidate}")
                    raise e
                return
            for i_cd in range(n_candidates):
                node_name = node_names[i_param]
                candidate[node_name] = parameters[i_cd][node_name]
                generate_candidates_recursively(i_param + 1, candidate)
                candidate.pop(node_name)

        generate_candidates_recursively(0, dict())
        return all_candidates