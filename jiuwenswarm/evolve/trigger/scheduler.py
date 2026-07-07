# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Periodic evolution scheduler for AgentServer integration.

Runs as an asyncio task inside the AgentServer event loop,
triggering the evolution pipeline on a configurable interval.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class EvolutionScheduler:
    """Periodic scheduler that triggers evolution on a fixed interval.

    Designed to run as a long-lived asyncio task inside AgentServer.
    """

    def __init__(
        self,
        pipeline: object,
        sampler: object,
        interval_seconds: int = 3600,
    ) -> None:
        self._pipeline = pipeline
        self._sampler = sampler
        self._interval = interval_seconds

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """Run the scheduler loop until *stop_event* is set.

        Args:
            stop_event: An ``asyncio.Event`` that is set on shutdown.
        """
        logger.info(
            "EvolutionScheduler: started (interval=%ds)", self._interval
        )
        while not stop_event.is_set():
            try:
                batch = self._sampler.sample()
                if batch.trace_ids:
                    logger.info(
                        "EvolutionScheduler: running pipeline for batch %s "
                        "(%d traces)",
                        batch.batch_id,
                        len(batch.trace_ids),
                    )
                    result = await self._pipeline.run(batch)
                    logger.info(
                        "EvolutionScheduler: batch %s complete — "
                        "%d proposals, %d applied, %d errors",
                        batch.batch_id,
                        len(result.proposals),
                        result.applied_count,
                        len(result.errors),
                    )
                else:
                    logger.info(
                        "EvolutionScheduler: no traces to process, "
                        "skipping this cycle"
                    )
            except Exception as exc:
                logger.error(
                    "EvolutionScheduler: pipeline run failed: %s",
                    exc,
                    exc_info=True,
                )

            # Sleep with stop_event awareness
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self._interval
                )
                # stop_event was set
                break
            except asyncio.TimeoutError:
                # Normal timeout — continue to next cycle
                pass

        logger.info("EvolutionScheduler: stopped")


async def run_evolution_scheduler(
    stop_event: asyncio.Event,
    pipeline: object,
    sampler: object,
    interval_seconds: int = 3600,
) -> None:
    """Convenience coroutine that creates and runs an EvolutionScheduler.

    Args:
        stop_event: Set on AgentServer shutdown.
        pipeline: Configured EvolutionPipeline instance.
        sampler: Configured TraceSampler instance.
        interval_seconds: Seconds between evolution runs.
    """
    scheduler = EvolutionScheduler(
        pipeline=pipeline,
        sampler=sampler,
        interval_seconds=interval_seconds,
    )
    await scheduler.run_forever(stop_event)
