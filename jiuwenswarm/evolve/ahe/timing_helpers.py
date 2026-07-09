# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Timing helper utilities for AHE algorithm."""

import time
import functools
from typing import Callable, Any
from jiuwenswarm.evolve.ahe.timing_stats import get_timing_stats


def timed_stage(stage_name: str):
    """Decorator to automatically time a stage method.

    Usage:
        @timed_stage("DIAG")
        async def diagnose_trace(...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            stats = get_timing_stats()
            stats.start_stage(stage_name)

            try:
                result = await func(*args, **kwargs)

                # Try to get trace count from result
                trace_count = 0
                if isinstance(result, list):
                    trace_count = len(result)
                elif isinstance(result, dict) and 'trace_count' in result:
                    trace_count = result['trace_count']

                stats.end_stage(stage_name, trace_count=trace_count)
                return result
            except Exception:
                # Still record timing on error
                stats.end_stage(stage_name, trace_count=0)
                raise

        return wrapper
    return decorator


def timed_trace_operation(stage_name: str, trace_id_getter: Callable[[Any], str] = None):
    """Decorator for per-trace timing within a stage.

    Args:
        stage_name: Stage name for timing stats
        trace_id_getter: Function to extract trace_id from first argument
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            stats = get_timing_stats()
            start = time.time()

            result = await func(*args, **kwargs)
            duration = time.time() - start

            # Extract trace_id
            trace_id = "unknown"
            if trace_id_getter and args:
                trace_id = trace_id_getter(args[0])
            elif args and isinstance(args[0], dict):
                trace_id = args[0].get("trace_id", "unknown")

            stats.add_stage_detail(stage_name, trace_id, duration)

            return result

        return wrapper
    return decorator