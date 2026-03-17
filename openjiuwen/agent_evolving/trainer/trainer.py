# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Training orchestrator for self-evolving agents.

Unified access to evolvable operators via agent.get_operators().
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

from tqdm import tqdm

from openjiuwen.agent_evolving.dataset import Case, EvaluatedCase, CaseLoader
from openjiuwen.agent_evolving.constant import TuneConstant
from openjiuwen.agent_evolving.updater import Updater
from openjiuwen.agent_evolving.evaluator import BaseEvaluator
from openjiuwen.agent_evolving.trainer.progress import Progress, Callbacks
from openjiuwen.agent_evolving.trajectory import ExecutionSpec, Trajectory, Updates, TracerTrajectoryExtractor
from openjiuwen.agent_evolving.checkpointing import FileCheckpointStore, DefaultCheckpointManager
from openjiuwen.core.operator import Operator
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent import BaseAgent


class Trainer:
    """
    Orchestrates "evaluate -> update -> writeback" self-evolution cycle.

    Accepts Updater and BaseEvaluator, manages checkpoint/resume.
    """

    def __init__(
        self,
        *,
        updater: Updater,
        evaluator: BaseEvaluator,
        extractor: Optional[TracerTrajectoryExtractor] = None,
        callbacks: Optional[Callbacks] = None,
        num_parallel: int = TuneConstant.default_parallel_num,
        early_stop_score: float = TuneConstant.default_early_stop_score,
        # checkpoint (disabled by default)
        checkpoint_dir: Optional[str] = None,
        resume_from: Optional[str] = None,
        checkpoint_every_n_epochs: int = 1,
        checkpoint_on_improve: bool = True,
        checkpoint_manager: Any = None,
    ):
        """
        Args:
            updater: Generates parameter updates from trajectory and evaluation.
            evaluator: Scores model output against expected answers.
            extractor: Extracts Trajectory from Session; defaults to TracerTrajectoryExtractor.
            callbacks: Lifecycle hooks (on_train_begin/end, etc).
            num_parallel: Parallelism for inference and evaluation.
            early_stop_score: Stop training when validation score reaches this value.
            checkpoint_dir: Directory for checkpoints; None disables checkpointing.
            resume_from: Checkpoint path to resume training.
            checkpoint_every_n_epochs: Save checkpoint every N epochs.
            checkpoint_on_improve: Also save when validation improves.
            checkpoint_manager: Custom manager; defaults to DefaultCheckpointManager.
        """
        self._updater = updater
        self._evaluator = evaluator
        self._extractor = extractor or TracerTrajectoryExtractor()
        self._callbacks = callbacks or Callbacks()

        self._num_parallel = int(num_parallel)
        self._early_stop_score = float(early_stop_score)

        self._checkpoint_store = FileCheckpointStore(checkpoint_dir) if checkpoint_dir else None
        self._resume_from = resume_from
        self._checkpoint_manager = checkpoint_manager or (
            DefaultCheckpointManager(
                save_every_n_epochs=checkpoint_every_n_epochs,
                save_on_improve=checkpoint_on_improve,
            )
            if self._checkpoint_store is not None
            else None
        )

    def set_callbacks(self, callbacks: Callbacks):
        """Set training lifecycle callbacks (e.g., progress printing, metric logging)."""
        self._callbacks = callbacks

    @staticmethod
    def _mean_score(evaluated: List[EvaluatedCase]) -> float:
        if not evaluated:
            return 0.0
        return sum(c.score for c in evaluated) / len(evaluated)

    def _bind_updater(self, operators: Dict[str, Any], config: Dict[str, Any]) -> int:
        bind = getattr(self._updater, "bind", None)
        if not callable(bind):
            return 0
        bound_n = bind(operators, **config)
        return int(bound_n or 0)

    def _updater_requires_forward(self) -> bool:
        """Check if updater needs forward execution on train_cases."""
        requires = getattr(self._updater, "requires_forward_data", None)
        if callable(requires):
            return requires()
        return True  # Default: requires forward data for backward compatibility

    def _resume_if_needed(self, agent: BaseAgent, progress: Progress) -> None:
        if self._checkpoint_store is None or self._checkpoint_manager is None or not self._resume_from:
            return
        if not os.path.exists(self._resume_from):
            return

        ckpt = self._checkpoint_store.load_checkpoint(self._resume_from)
        restored = self._checkpoint_manager.restore(agent=agent, checkpoint=ckpt)
        progress.start_epoch = int(restored.get("start_epoch", 0))
        progress.best_score = float(restored.get("best_score", 0.0))

        load_state = getattr(self._updater, "load_state", None)
        if callable(load_state):
            load_state(getattr(ckpt, "updater_state", {}) or {})

        logger.info(f"[resume] epoch={progress.start_epoch} best={progress.best_score}")

    def _save_checkpoint_if_needed(self, agent: BaseAgent, progress: Progress, *, improved: bool) -> None:
        if self._checkpoint_store is None or self._checkpoint_manager is None:
            return
        if not self._checkpoint_manager.should_save(epoch=progress.current_epoch, improved=improved):
            return

        ckpt = self._checkpoint_manager.build_checkpoint(
            agent=agent,
            progress=progress,
            updater_state=self._updater.get_state(),
        )
        path = self._checkpoint_store.save_checkpoint(ckpt, filename="latest.json")
        logger.info(f"[checkpoint] saved: {path}")

    def train(
        self,
        *,
        agent: BaseAgent,
        train_cases: Optional[CaseLoader] = None,
        val_cases: Optional[CaseLoader] = None,
        num_iterations: int = TuneConstant.default_iteration_num,
        **kwargs: Any,
    ) -> BaseAgent:
        """
        Execute self-evolving training: validation baseline evaluation -> multiple rounds of
        "training forward -> updater update -> validation evaluation -> checkpoint".

        Args:
            agent: Agent to optimize (must implement get_operators() and support invoke with session).
            train_cases: Training case loader; optional for black-box optimizers that generate data internally.
            val_cases: Validation case loader; uses train_cases if not provided; optional for black-box optimizers.
            num_iterations: Maximum training epochs.
            **kwargs: Pass through to updater.update config.

        Returns:
            Agent after training (internal parameters updated by updater).
        """
        progress = Progress(max_epoch=num_iterations)
        val_cases = val_cases or train_cases

        operators = self._get_operator_registry(agent)
        if self._bind_updater(operators, config=kwargs) == 0:
            logger.error("[Trainer] no operator matches updater targets; soft-exit without training.")
            return agent

        self._resume_if_needed(agent, progress)

        if self._updater_requires_forward():
            progress.current_epoch_score, cur_epoch_evaluated = self.evaluate(agent, val_cases)
            progress.best_score = max(progress.best_score, progress.current_epoch_score)
        else:
            # Black-box optimizer: skip initial validation baseline (uses internal evaluation)
            progress.current_epoch_score = 0.0
            cur_epoch_evaluated = []

        self._callbacks.on_train_begin(agent, progress, cur_epoch_evaluated)

        if progress.best_score >= self._early_stop_score:
            self._callbacks.on_train_end(agent, progress, cur_epoch_evaluated)
            return agent

        for _ in progress.run_epoch():
            self._callbacks.on_train_epoch_begin(agent, progress)

            if self._updater_requires_forward():
                score, evaluated, trajectories, _sessions = self.forward(agent, train_cases)
                progress.current_epoch_score = score
            else:
                # Black-box optimizer: skip forward, pass empty data (optimizer generates internally)
                trajectories, evaluated = [], []
                progress.current_epoch_score = 0.0

            updated = self._updater.update(trajectories, evaluated, config=kwargs)

            if isinstance(updated, list):
                val_score, val_evaluated = self._select_best_candidate_on_val(
                    agent=agent,
                    operators=operators,
                    candidates=updated,
                    val_cases=val_cases,
                )
            else:
                updates: Updates = updated or {}
                self.apply_updates(operators, updates)
                val_score, val_evaluated = self.evaluate(agent, val_cases)

            improved = val_score > progress.best_score
            if improved:
                progress.best_score = val_score

            self._callbacks.on_train_epoch_end(agent, progress, val_evaluated)

            self._save_checkpoint_if_needed(agent, progress, improved=improved)

            if progress.best_score >= self._early_stop_score:
                break

        self._callbacks.on_train_end(agent, progress, cur_epoch_evaluated)

        return agent

    def _select_best_candidate_on_val(
        self,
        *,
        agent: BaseAgent,
        operators: Dict[str, Operator],
        candidates: List[Updates],
        val_cases: CaseLoader,
    ) -> Tuple[float, List[EvaluatedCase]]:
        """
        Candidate evaluation and selection (Scheme A):
        - Evaluate each candidate updates on validation, select best
        - Use Operator.get_state/load_state for snapshot rollback, avoid copying Agent
        - Restore operators to best state finally (commit best)
        """
        if not candidates:
            return self.evaluate(agent, val_cases)

        base_state = self._snapshot_operators_state(operators)

        best_score = float("-inf")
        best_evaluated: List[EvaluatedCase] = []
        best_state: Optional[Dict[str, Dict[str, Any]]] = None

        for idx, cand_updates in enumerate(candidates):
            self._restore_operators_state(operators, base_state)
            self.apply_updates(operators, cand_updates or {})

            cand_score, cand_evaluated = self.evaluate(agent, val_cases)
            logger.info(f"[candidate] idx={idx} val_score={cand_score:.4f}")

            if cand_score > best_score:
                best_score = cand_score
                best_evaluated = cand_evaluated
                best_state = self._snapshot_operators_state(operators)

        if best_state is not None:
            self._restore_operators_state(operators, best_state)
            return best_score, best_evaluated

        self._restore_operators_state(operators, base_state)
        return self.evaluate(agent, val_cases)

    def forward(
        self,
        agent: BaseAgent,
        cases: Optional[CaseLoader],
    ) -> Tuple[float, List[EvaluatedCase], List[Trajectory], List[Any]]:
        """
        Single forward pass on cases: inference -> evaluation -> extract trajectory from each Session.

        Returns:
            (average score, evaluation results list, trajectory list, Session list).
        """
        if cases is None or not cases.get_cases():
            return 0.0, [], [], []
        predicts, sessions = self.predict(agent, cases)
        evaluated = self._evaluator.batch_evaluate(cases.get_cases(), predicts, num_parallel=self._num_parallel)
        score = self._mean_score(evaluated)

        trajectories: List[Trajectory] = []
        for case, sess in zip(cases.get_cases(), sessions):
            exec_spec = ExecutionSpec(case_id=case.case_id, execution_id=str(uuid.uuid4()))
            trajectories.append(self._extractor.extract(sess, exec_spec))
        return score, evaluated, trajectories, sessions

    def evaluate(self, agent: BaseAgent, cases: Optional[CaseLoader]) -> Tuple[float, List[EvaluatedCase]]:
        """
        Run inference and evaluation on cases, return average score and
        evaluation results (no trajectory extraction).
        """
        if cases is None or not cases.get_cases():
            return 0.0, []
        predicts = self.predict_only(agent, cases)
        evaluated = self._evaluator.batch_evaluate(cases.get_cases(), predicts, num_parallel=self._num_parallel)
        score = self._mean_score(evaluated)
        return score, evaluated

    def predict_only(self, agent: BaseAgent, cases: Optional[CaseLoader]) -> List[Dict]:
        """Run inference only, return list of model outputs per case (no Session return)."""
        if cases is None:
            return []
        predicts, _ = self.predict(agent, cases)
        return predicts

    def predict(self, agent: BaseAgent, cases: Optional[CaseLoader]) -> Tuple[List[Dict], List[Any]]:
        """
        Run agent.invoke on each case (with Session), parallelism controlled by num_parallel.

        Returns:
            (model output list, Session list); Session used for subsequent
            trajectory extraction from tracer.
        """
        if cases is None:
            return [], []
        case_list = cases.get_cases()

        async def run_one(case: Case, sem: asyncio.Semaphore):
            """Single case: pre-create session -> invoke -> return (output, session)."""
            async with sem:
                session = create_agent_session()
                try:
                    res = await agent.invoke({**case.inputs, "conversation_id": case.case_id}, session=session)
                except Exception as e:
                    res = dict(error=f"Get wrong result due to {str(e)}")
                return res, session

        async def run_all():
            """Concurrently execute all cases, limited by num_parallel."""
            sem = asyncio.Semaphore(min(self._num_parallel, len(case_list)) if case_list else 1)
            tasks = [run_one(c, sem) for c in case_list]
            return await asyncio.gather(*tasks)

        results = asyncio.run(run_all())
        predicts = [r[0] for r in results]
        sessions = [r[1] for r in results]
        predicts = list(tqdm(predicts, desc="forward", total=len(predicts)))
        return predicts, sessions

    @staticmethod
    def apply_updates(operators: Dict[str, Operator], updates: Updates) -> None:
        """
        Apply updater-generated updates to operator registry.

        For SingleDimUpdater (BaseOptimizer direct writeback), updates usually empty, skipped here.
        """
        for (operator_id, target), value in updates.items():
            op = operators.get(operator_id)
            if op is not None and value is not None:
                op.set_parameter(target, value)

    @staticmethod
    def _snapshot_operators_state(operators: Dict[str, Operator]) -> Dict[str, Dict[str, Any]]:
        """Snapshot current operators_state (operator_id -> state), used for candidate evaluation rollback/commit."""
        out: Dict[str, Dict[str, Any]] = {}
        for op_id, op in (operators or {}).items():
            out[op_id] = op.get_state()
        return out

    @staticmethod
    def _restore_operators_state(operators: Dict[str, Operator], state: Dict[str, Dict[str, Any]]) -> None:
        """Restore operators_state (operator_id -> state)."""
        for op_id, st in state.items():
            op = operators.get(op_id)
            if op is None:
                continue
            op.load_state(st)

    @staticmethod
    def _get_operator_registry(agent: BaseAgent) -> Dict[str, Operator]:
        """
        Get operator registry from agent (operator_id -> Operator).

        Unified use of agent.get_operators(), returns Dict[operator_id, Operator];
        key must be operator_id to align with trajectory/Updates.
        """
        get_ops = getattr(agent, "get_operators", None)
        if not callable(get_ops):
            return {}
        return get_ops()
