# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Callback Framework Chain

Callback chain execution with rollback support.
"""

import asyncio
import logging
from typing import (
    Callable,
    Dict,
    List,
    Optional,
)

from openjiuwen.core.runner.callback.enums import ChainAction
from openjiuwen.core.runner.callback.models import (
    CallbackInfo,
    ChainContext,
    ChainResult,
)


class CallbackChain:
    """Manages sequential execution of callbacks with rollback support.

    Provides ordered execution, error handling, and rollback capabilities
    for groups of related callbacks.

    Attributes:
        name: Chain identifier
        callbacks: List of callback information objects
        rollback_handlers: Mapping of callbacks to rollback functions
        error_handlers: Mapping of callbacks to error handlers
    """

    def __init__(self, name: str = ""):
        """Initialize callback chain.

        Args:
            name: Optional chain name
        """
        self.name = name
        self.callbacks: List[CallbackInfo] = []
        self.rollback_handlers: Dict[Callable, Callable] = {}
        self.error_handlers: Dict[Callable, Callable] = {}

    def add(
            self,
            callback_info: CallbackInfo,
            rollback_handler: Optional[Callable] = None,
            error_handler: Optional[Callable] = None
    ) -> None:
        """Add callback to the chain.

        Args:
            callback_info: Callback metadata and configuration
            rollback_handler: Optional function to call on rollback
            error_handler: Optional function to call on error
        """
        self.callbacks.append(callback_info)
        self.callbacks.sort(key=lambda x: x.priority, reverse=True)

        if rollback_handler:
            self.rollback_handlers[callback_info.callback] = rollback_handler
        if error_handler:
            self.error_handlers[callback_info.callback] = error_handler

    def remove(self, callback: Callable) -> None:
        """Remove callback from the chain.

        Args:
            callback: Callback function to remove
        """
        self.callbacks = [ci for ci in self.callbacks if ci.callback != callback]
        self.rollback_handlers.pop(callback, None)
        self.error_handlers.pop(callback, None)

    async def execute(self, context: ChainContext) -> ChainResult:
        """Execute the callback chain.

        Executes callbacks in priority order, passing results between them.
        Supports retry logic, error handling, and rollback on failure.

        Args:
            context: Chain execution context

        Returns:
            ChainResult with execution outcome
        """
        executed_callbacks = []

        for i, callback_info in enumerate(self.callbacks):
            if not callback_info.enabled:
                continue

            context.current_index = i
            callback = callback_info.callback

            # Retry loop
            for attempt in range(callback_info.max_retries + 1):
                try:
                    # Prepare arguments - chain previous result
                    if context.results:
                        args = (context.get_last_result(),) + context.initial_args
                    else:
                        args = context.initial_args

                    kwargs = context.initial_kwargs.copy()
                    kwargs['_chain_context'] = context

                    # Execute with timeout if specified
                    if callback_info.timeout:
                        result = await asyncio.wait_for(
                            callback(*args, **kwargs),
                            timeout=callback_info.timeout
                        )
                    else:
                        result = await callback(*args, **kwargs)

                    # Process result
                    if isinstance(result, ChainResult):
                        if result.action == ChainAction.BREAK:
                            context.results.append(result.result)
                            return ChainResult(
                                ChainAction.BREAK,
                                result=result.result,
                                context=context
                            )
                        elif result.action == ChainAction.RETRY:
                            continue
                        elif result.action == ChainAction.ROLLBACK:
                            await self._rollback(executed_callbacks, context)
                            return ChainResult(
                                ChainAction.ROLLBACK,
                                context=context,
                                error=result.error
                            )
                        else:
                            context.results.append(result.result)
                    else:
                        context.results.append(result)

                    executed_callbacks.append(callback)

                    # Handle once-only callbacks
                    if callback_info.once:
                        callback_info.enabled = False

                    break  # Success, exit retry loop

                except asyncio.TimeoutError:
                    logging.error(f"Callback {callback.__name__} timed out")
                    if attempt < callback_info.max_retries:
                        await asyncio.sleep(callback_info.retry_delay)
                        continue
                    else:
                        await self._rollback(executed_callbacks, context)
                        return ChainResult(
                            ChainAction.ROLLBACK,
                            context=context,
                            error=asyncio.TimeoutError("Callback timeout")
                        )

                except Exception as e:
                    # Try error handler
                    if callback in self.error_handlers:
                        try:
                            error_result = await self.error_handlers[callback](e, context)
                            if error_result:
                                context.results.append(error_result)
                                executed_callbacks.append(callback)
                                break
                        except Exception as handler_error:
                            logging.error(f"Error handler failed: {handler_error}")

                    # Retry if attempts remaining
                    if attempt < callback_info.max_retries:
                        logging.info(
                            f"Retrying {callback.__name__} (attempt {attempt + 1})"
                        )
                        await asyncio.sleep(callback_info.retry_delay)
                        continue

                    # Rollback on final failure
                    await self._rollback(executed_callbacks, context)
                    return ChainResult(ChainAction.ROLLBACK, context=context, error=e)

        context.is_completed = True
        return ChainResult(
            ChainAction.CONTINUE,
            result=context.get_last_result(),
            context=context
        )

    async def _rollback(
            self,
            executed_callbacks: List[Callable],
            context: ChainContext
    ) -> None:
        """Execute rollback handlers for executed callbacks.

        Args:
            executed_callbacks: List of callbacks that were executed
            context: Chain execution context
        """
        context.is_rolled_back = True

        for callback in reversed(executed_callbacks):
            if callback in self.rollback_handlers:
                try:
                    await self.rollback_handlers[callback](context)
                except Exception as e:
                    logging.error(f"Rollback failed for {callback.__name__}: {e}")
