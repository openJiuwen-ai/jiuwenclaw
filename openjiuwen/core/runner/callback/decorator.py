# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Decorator implementations for AsyncCallbackFramework.

Extracted decorator logic for on(), emit_before(), emit_after(),
and emit_around() to keep framework.py focused on registration and
execution flow.

Also provides transform_io decorator for modifying function inputs/outputs
with full async and generator support. Transform logic can be provided
either as direct callables (input_transform/output_transform) or as
event names (input_event/output_event); when using events, the framework
triggers the event and uses the last callback result as the transformed
input or output.

Also provides wrap/on_wrap decorators for building explicit WrapHandler
chains around functions. Each WrapHandler receives ``call_next`` as its
first argument and decides when and whether to invoke the rest of the
chain, enabling pre/post processing, short-circuiting, and item
transformation for both regular functions and generators.
"""

import inspect
from functools import wraps
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Literal,
    Optional,
    Protocol,
    Set,
)

from openjiuwen.core.runner.callback.filters import EventFilter

# Sentinel value returned by trigger_transform when no transform callbacks are registered.
_TRANSFORM_NOOP = object()

# Type aliases for transform callables (sync or async).
InputTransform = Callable[
    ...,
    tuple[tuple[Any, ...], dict[str, Any]]
    | Awaitable[tuple[tuple[Any, ...], dict[str, Any]]],
]
OutputTransform = Callable[[Any], Any | Awaitable[Any]]


class WrapHandler(Protocol):
    """Callable placed in a wrap chain around another function.

    Receives ``call_next`` as its first positional-only argument — the next
    callable in the chain, ultimately reaching the original function — followed
    by the original ``*args`` and ``**kwargs``.

    The handler controls the full invocation: it may modify arguments before
    forwarding, transform or replace the result, or short-circuit the chain by
    never calling ``call_next``.

    Return type depends on the kind of the wrapped function:

    * **Regular function** — must be an ``async def`` returning
      ``Awaitable[Any]``.
    * **Generator function** — must be an ``async def`` that is also an async
      generator (contains ``yield``), returning ``AsyncIterator[Any]``.
    """

    def __call__(
        self,
        call_next: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Awaitable[Any] | AsyncIterator[Any]:
        ...


def _bind_args_no_duplicate(
    func: Callable[..., Any],
    new_args: tuple[Any, ...],
    new_kwargs: dict[str, Any],
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Prefer keyword over position to avoid duplicate argument.

    When input transform returns (args, kwargs), passing both to func can cause
    "multiple values for argument" if the same parameter appears in both.
    We prefer keyword: for the first len(args) parameters of func, if the
    parameter name is in new_kwargs, pass it only by keyword and drop that
    positional slot; otherwise keep the positional value.
    """
    try:
        sig = inspect.signature(func)
    except (ValueError, TypeError):
        return new_args, new_kwargs
    param_names: list[str] = []
    for name, p in sig.parameters.items():
        if p.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            break
        param_names.append(name)
    n_pos = min(len(new_args), len(param_names))
    # Prefer keyword: drop positional for params that are in new_kwargs.
    keep_pos = [
        new_args[i]
        for i in range(n_pos)
        if param_names[i] not in new_kwargs
    ]
    # Remaining positional args (e.g. *args or extra positionals)
    extra_pos = new_args[n_pos:]
    call_args = tuple(keep_pos) + extra_pos
    return call_args, dict(new_kwargs)


def create_on_decorator(
    framework: Any,
    event: str,
    *,
    priority: int = 0,
    once: bool = False,
    namespace: str = "default",
    tags: Optional[Set[str]] = None,
    filters: Optional[list[EventFilter]] = None,
    rollback_handler: Optional[Callable] = None,
    error_handler: Optional[Callable] = None,
    max_retries: int = 0,
    retry_delay: float = 0.0,
    timeout: Optional[float] = None,
    callback_type: str = "",
) -> Callable[[Callable[..., Awaitable[Any]]], Callable]:
    """Create decorator that registers an async callback with the framework.

    Used by AsyncCallbackFramework.on(). Registers the function synchronously
    and returns a wrapper that delegates to the original function.

    Args:
        framework: AsyncCallbackFramework instance (has register_sync).
        event: Event name to listen for.
        priority: Execution priority (higher first).
        once: Execute only once then disable.
        namespace: Namespace for grouping.
        tags: Set of tags for filtering.
        filters: List of filters to apply.
        rollback_handler: Function to call on rollback.
        error_handler: Function to call on error.
        max_retries: Maximum retry attempts.
        retry_delay: Delay between retries in seconds.
        timeout: Execution timeout in seconds.
        callback_type: Semantic type marker, e.g. "transform".

    Returns:
        A decorator that registers the wrapped function and returns the wrapper.
    """

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable:
        callback_info = framework.register_sync(
            event,
            func,
            priority=priority,
            once=once,
            namespace=namespace,
            tags=tags,
            filters=filters,
            rollback_handler=rollback_handler,
            error_handler=error_handler,
            max_retries=max_retries,
            retry_delay=retry_delay,
            timeout=timeout,
            callback_type=callback_type,
        )

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await func(*args, **kwargs)

        callback_info.wrapper = wrapper
        return wrapper

    return decorator


async def _do_trigger(
    framework: Any,
    event: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    pass_args: bool = True,
    extra: Optional[dict[str, Any]] = None,
    extra_kwargs: Optional[dict[str, Any]] = None,
) -> None:
    """Unified trigger helper to eliminate repetitive patterns."""
    merged = {**kwargs, **(extra or {}), **(extra_kwargs or {})}
    if pass_args:
        await framework.trigger(event, *args, **merged)
    else:
        await framework.trigger(event, **merged)


def create_emit_before_decorator(
    framework: Any,
    event: str,
    *,
    pass_args: bool = True,
    extra_kwargs: Optional[dict[str, Any]] = None,
) -> Callable[[Callable[..., Any]], Callable]:
    """Create decorator that triggers event BEFORE the decorated function is called.

    Supports async functions, async generators, sync functions (promoted to
    async), and sync generators (promoted to async generators).

    Args:
        framework: AsyncCallbackFramework instance (has trigger).
        event: Event name to trigger.
        pass_args: Whether to pass function arguments to callbacks.
        extra_kwargs: Additional keyword arguments merged into every trigger call.

    Returns:
        A decorator that triggers the event then runs the wrapped function.
    """

    def decorator(func: Callable[..., Any]) -> Callable:
        if inspect.isasyncgenfunction(func):
            @wraps(func)
            async def async_gen_wrapper(*args: Any, **kwargs: Any) -> Any:
                await _do_trigger(framework, event, args, kwargs, pass_args=pass_args,
                                  extra_kwargs=extra_kwargs)
                async for item in func(*args, **kwargs):
                    yield item

            return async_gen_wrapper

        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                await _do_trigger(framework, event, args, kwargs, pass_args=pass_args,
                                  extra_kwargs=extra_kwargs)
                return await func(*args, **kwargs)

            return async_wrapper

        if inspect.isgeneratorfunction(func):
            @wraps(func)
            async def sync_gen_promoted_wrapper(*args: Any, **kwargs: Any) -> Any:
                await _do_trigger(framework, event, args, kwargs, pass_args=pass_args,
                                  extra_kwargs=extra_kwargs)
                for item in func(*args, **kwargs):
                    yield item

            return sync_gen_promoted_wrapper

        @wraps(func)
        async def sync_promoted_wrapper(*args: Any, **kwargs: Any) -> Any:
            await _do_trigger(framework, event, args, kwargs, pass_args=pass_args,
                              extra_kwargs=extra_kwargs)
            return func(*args, **kwargs)

        return sync_promoted_wrapper

    return decorator


def create_emit_after_decorator(
    framework: Any,
    event: str,
    *,
    result_key: str = "result",
    item_key: str = "item",
    pass_args: bool = False,
    stream_mode: Literal["per_item", "once"] = "per_item",
    extra_kwargs: Optional[dict[str, Any]] = None,
) -> Callable[[Callable[..., Any]], Callable]:
    """Create decorator that triggers event AFTER the decorated function completes.

    For regular functions: triggers event with the result.
    For generators: triggers event for each yielded item (per_item mode)
    or once with all collected items (once mode).

    Supports async functions, async generators, sync functions (promoted to
    async), and sync generators (promoted to async generators).

    Args:
        framework: AsyncCallbackFramework instance (has trigger, enable_logging, logger).
        event: Event name to trigger.
        result_key: Keyword argument name for the result (regular functions).
        item_key: Keyword argument name for each yielded item (generators).
        pass_args: Whether to include original args in event.
        stream_mode: For generators - "per_item" triggers per item, "once" triggers
            after all items are yielded with collected results.
        extra_kwargs: Additional keyword arguments merged into every trigger call.

    Returns:
        A decorator that runs the function then triggers the event with result.
    """

    def decorator(func: Callable[..., Any]) -> Callable:
        if inspect.isasyncgenfunction(func):
            if stream_mode == "once":

                @wraps(func)
                async def async_gen_once_wrapper(*args: Any, **kwargs: Any) -> Any:
                    collected: list[Any] = []
                    async for item in func(*args, **kwargs):
                        collected.append(item)
                        yield item
                    await _do_trigger(
                        framework, event, args, kwargs,
                        pass_args=pass_args,
                        extra={result_key: collected},
                        extra_kwargs=extra_kwargs,
                    )

                return async_gen_once_wrapper

            @wraps(func)
            async def async_gen_per_item_wrapper(*args: Any, **kwargs: Any) -> Any:
                async for item in func(*args, **kwargs):
                    await _do_trigger(
                        framework, event, (), {},
                        pass_args=False,
                        extra={item_key: item},
                        extra_kwargs=extra_kwargs,
                    )
                    yield item

            return async_gen_per_item_wrapper

        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                result = await func(*args, **kwargs)
                await _do_trigger(
                    framework, event, args, kwargs,
                    pass_args=pass_args,
                    extra={result_key: result},
                    extra_kwargs=extra_kwargs,
                )
                return result

            return async_wrapper

        if inspect.isgeneratorfunction(func):
            if stream_mode == "once":

                @wraps(func)
                async def sync_gen_once_wrapper(*args: Any, **kwargs: Any) -> Any:
                    collected: list[Any] = []
                    for item in func(*args, **kwargs):
                        collected.append(item)
                        yield item
                    await _do_trigger(
                        framework, event, args, kwargs,
                        pass_args=pass_args,
                        extra={result_key: collected},
                        extra_kwargs=extra_kwargs,
                    )

                return sync_gen_once_wrapper

            @wraps(func)
            async def sync_gen_per_item_wrapper(*args: Any, **kwargs: Any) -> Any:
                for item in func(*args, **kwargs):
                    await _do_trigger(
                        framework, event, (), {},
                        pass_args=False,
                        extra={item_key: item},
                        extra_kwargs=extra_kwargs,
                    )
                    yield item

            return sync_gen_per_item_wrapper

        @wraps(func)
        async def sync_promoted_wrapper(*args: Any, **kwargs: Any) -> Any:
            result = func(*args, **kwargs)
            await _do_trigger(
                framework, event, args, kwargs,
                pass_args=pass_args,
                extra={result_key: result},
                extra_kwargs=extra_kwargs,
            )
            return result

        return sync_promoted_wrapper

    return decorator


def create_emit_around_decorator(
    framework: Any,
    before_event: str,
    after_event: str,
    *,
    pass_args: bool = True,
    pass_result: bool = True,
    on_error_event: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable]:
    """Create decorator that triggers events before and after function execution.

    Supports async functions, async generators, sync functions (promoted to
    async), and sync generators (promoted to async generators). For generators,
    after_event is triggered after all items are yielded (with collected results
    if pass_result=True).

    Args:
        framework: AsyncCallbackFramework instance (has trigger).
        before_event: Event to trigger before execution.
        after_event: Event to trigger after successful execution.
        pass_args: Whether to pass function arguments to events.
        pass_result: Whether to pass result to after_event.
        on_error_event: Optional event to trigger on error.

    Returns:
        A decorator that triggers before/after/error events around the function.
    """

    async def _trigger_after(
        args: tuple[Any, ...], kwargs: dict[str, Any], result: Any
    ) -> None:
        if pass_result:
            await _do_trigger(
                framework, after_event, args, kwargs,
                pass_args=pass_args,
                extra={"result": result},
            )
        else:
            await _do_trigger(framework, after_event, args, kwargs, pass_args=pass_args)

    async def _trigger_error(
        args: tuple[Any, ...], kwargs: dict[str, Any], e: Exception
    ) -> None:
        if on_error_event:
            await _do_trigger(
                framework, on_error_event, args, kwargs,
                pass_args=pass_args,
                extra={"error": e},
            )

    def decorator(func: Callable[..., Any]) -> Callable:
        if inspect.isasyncgenfunction(func):
            @wraps(func)
            async def async_gen_wrapper(*args: Any, **kwargs: Any) -> Any:
                await _do_trigger(framework, before_event, args, kwargs, pass_args=pass_args)
                collected: list[Any] = []
                try:
                    async for item in func(*args, **kwargs):
                        collected.append(item)
                        yield item
                    await _trigger_after(args, kwargs, collected)
                except Exception as e:
                    await _trigger_error(args, kwargs, e)
                    raise

            return async_gen_wrapper

        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                await _do_trigger(framework, before_event, args, kwargs, pass_args=pass_args)
                try:
                    result = await func(*args, **kwargs)
                    await _trigger_after(args, kwargs, result)
                    return result
                except Exception as e:
                    await _trigger_error(args, kwargs, e)
                    raise

            return async_wrapper

        if inspect.isgeneratorfunction(func):
            @wraps(func)
            async def sync_gen_promoted_wrapper(*args: Any, **kwargs: Any) -> Any:
                await _do_trigger(framework, before_event, args, kwargs, pass_args=pass_args)
                collected: list[Any] = []
                try:
                    for item in func(*args, **kwargs):
                        collected.append(item)
                        yield item
                    await _trigger_after(args, kwargs, collected)
                except Exception as e:
                    await _trigger_error(args, kwargs, e)
                    raise

            return sync_gen_promoted_wrapper

        @wraps(func)
        async def sync_promoted_wrapper(*args: Any, **kwargs: Any) -> Any:
            await _do_trigger(framework, before_event, args, kwargs, pass_args=pass_args)
            try:
                result = func(*args, **kwargs)
                await _trigger_after(args, kwargs, result)
                return result
            except Exception as e:
                await _trigger_error(args, kwargs, e)
                raise

        return sync_promoted_wrapper

    return decorator


async def _apply_output_transform(
    value: Any, transform: OutputTransform
) -> Any:
    """Apply output transform; await if transform is async."""
    out = transform(value)
    if inspect.iscoroutine(out):
        return await out
    return out


def _make_transform_io_decorator(
    func: Callable[..., Any],
    *,
    input_fn: Callable[
        [tuple[Any, ...], dict[str, Any]],
        Awaitable[tuple[tuple[Any, ...], dict[str, Any]]],
    ],
    output_fn: Callable[[Any], Awaitable[Any]],
    output_source_fn: Optional[Callable],
    output_mode: Literal["frame", "generator"],
) -> Callable[..., Any]:
    """Core builder for transform_io wrappers (both direct and event-based).

    Args:
        func: The function to wrap.
        input_fn: Async callable transforming (args, kwargs) -> (new_args, new_kwargs).
        output_fn: Async callable transforming a single output value.
        output_source_fn: Async callable receiving an AsyncIterator source and
            returning an async iterable (generator mode only). None = passthrough.
        output_mode: "frame" applies output_fn per item; "generator" passes the
            entire source to output_source_fn.

    Returns:
        Wrapped function preserving func's metadata via functools.wraps.
    """
    func_async_gen = inspect.isasyncgenfunction(func)
    func_async = inspect.iscoroutinefunction(func)
    func_sync_gen = inspect.isgeneratorfunction(func)

    if output_mode == "generator" and not func_async_gen and not func_sync_gen:
        raise ValueError(
            "output_mode='generator' is only supported for generator "
            f"functions; got {func!r}"
        )

    # Branch 1: async gen + generator mode
    if func_async_gen and output_mode == "generator":

        @wraps(func)
        async def async_gen_wrapper_gen_mode(*args: Any, **kwargs: Any) -> Any:
            new_args, new_kwargs = await input_fn(args, kwargs)
            call_args, call_kwargs = _bind_args_no_duplicate(
                func, new_args, new_kwargs
            )
            source = func(*call_args, **call_kwargs)
            if output_source_fn is None:
                async for item in source:
                    yield item
            else:
                iterable = await output_source_fn(source)
                async for item in iterable:
                    yield item

        return async_gen_wrapper_gen_mode  # type: ignore[return-value]

    # Branch 2: async gen + frame mode
    if func_async_gen:

        @wraps(func)
        async def async_gen_wrapper(*args: Any, **kwargs: Any) -> Any:
            new_args, new_kwargs = await input_fn(args, kwargs)
            call_args, call_kwargs = _bind_args_no_duplicate(
                func, new_args, new_kwargs
            )
            async_gen = func(*call_args, **call_kwargs)
            if not inspect.isasyncgen(async_gen):
                raise TypeError(
                    "expected async generator function, "
                    f"got {type(async_gen)}"
                )
            async for item in async_gen:
                yield await output_fn(item)

        return async_gen_wrapper  # type: ignore[return-value]

    # Branch 3: async function
    if func_async:

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            new_args, new_kwargs = await input_fn(args, kwargs)
            call_args, call_kwargs = _bind_args_no_duplicate(
                func, new_args, new_kwargs
            )
            result = await func(*call_args, **call_kwargs)
            return await output_fn(result)

        return async_wrapper  # type: ignore[return-value]

    # Branch 4: sync gen + generator mode
    if func_sync_gen and output_mode == "generator":

        @wraps(func)
        async def sync_gen_promoted_gen_mode(*args: Any, **kwargs: Any) -> Any:
            new_args, new_kwargs = await input_fn(args, kwargs)
            call_args, call_kwargs = _bind_args_no_duplicate(
                func, new_args, new_kwargs
            )
            gen = func(*call_args, **call_kwargs)

            async def _async_adapter() -> Any:
                for item in gen:
                    yield item

            if output_source_fn is None:
                async for item in _async_adapter():
                    yield item
            else:
                iterable = await output_source_fn(_async_adapter())
                async for item in iterable:
                    yield item

        return sync_gen_promoted_gen_mode  # type: ignore[return-value]

    # Branch 5: sync gen + frame mode (promoted)
    if func_sync_gen:

        @wraps(func)
        async def sync_gen_promoted_wrapper(*args: Any, **kwargs: Any) -> Any:
            new_args, new_kwargs = await input_fn(args, kwargs)
            call_args, call_kwargs = _bind_args_no_duplicate(
                func, new_args, new_kwargs
            )
            gen = func(*call_args, **call_kwargs)
            if not inspect.isgenerator(gen):
                raise TypeError(
                    "expected generator function, got " f"{type(gen)}"
                )
            for item in gen:
                yield await output_fn(item)

        return sync_gen_promoted_wrapper  # type: ignore[return-value]

    # Branch 6: sync plain (promoted)
    @wraps(func)
    async def sync_promoted_wrapper(*args: Any, **kwargs: Any) -> Any:
        new_args, new_kwargs = await input_fn(args, kwargs)
        call_args, call_kwargs = _bind_args_no_duplicate(
            func, new_args, new_kwargs
        )
        result = func(*call_args, **call_kwargs)
        return await output_fn(result)

    return sync_promoted_wrapper


def create_transform_io_decorator(
    *,
    input_transform: Optional[InputTransform] = None,
    output_transform: Optional[OutputTransform] = None,
    output_mode: Literal["frame", "generator"] = "frame",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Create a decorator that can modify function inputs and/or outputs.

    Supports sync/async functions and sync/async generators. Input transform
    receives (args, kwargs) and returns (new_args, new_kwargs). Output
    transform is applied to the return value, or to each yielded item when
    the function returns a generator. Transforms may be sync or async.

    ``output_mode`` controls how ``output_transform`` is applied to generator
    functions:

    * ``'frame'`` (default): ``output_transform(item)`` is called once per
      yielded item. The transform may be sync or async.
    * ``'generator'``: ``output_transform(source)`` is called once with the
      entire source async iterator. The transform must be an async generator
      function that iterates *source* and yields new items. This allows
      stateful transformations, filtering, and 1-to-N item expansion.
      Sync generator functions are automatically promoted to async.

    Example (frame mode):
        >>> def add_one(*args, **kwargs):
        ...     k = dict(kwargs)
        ...     k["n"] = k.get("n", 0) + 1
        ...     return (args, k)
        >>> def double(x):
        ...     return x * 2
        >>> @create_transform_io_decorator(
        ...     input_transform=add_one,
        ...     output_transform=double,
        ... )
        ... async def compute(n: int):
        ...     return n
        >>> await compute(0)  # input n becomes 1, output 1*2 -> 2
        2

    Example (generator mode):
        >>> async def expand(source):
        ...     async for item in source:
        ...         yield item
        ...         yield item  # duplicate each item
        >>> @create_transform_io_decorator(
        ...     output_transform=expand,
        ...     output_mode='generator',
        ... )
        ... async def gen():
        ...     yield 1
        ...     yield 2
        >>> [item async for item in gen()]
        [1, 1, 2, 2]

    Args:
        input_transform: Optional. (args, kwargs) -> (new_args, new_kwargs).
            May be sync or async. Called before invoking the wrapped function.
        output_transform: Optional. In ``'frame'`` mode: value -> new_value,
            applied to each yielded item or the return value; may be sync or
            async. In ``'generator'`` mode: async generator function
            ``(source: AsyncIterator) -> AsyncIterator``, receives the full
            source iterator and yields transformed items.
        output_mode: ``'frame'`` (default) or ``'generator'``. Only affects
            generator functions; raises ``ValueError`` for non-generators.

    Returns:
        A decorator that wraps the function with input/output transformation.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func_sync_gen = inspect.isgeneratorfunction(func)
        func_async = inspect.iscoroutinefunction(func)
        func_async_gen = inspect.isasyncgenfunction(func)

        # --- sync paths (kept pure sync to preserve generator/function nature) ---

        def _run_input_transform_sync(
            args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> tuple[tuple[Any, ...], dict[str, Any]]:
            if input_transform is None:
                return args, kwargs
            out = input_transform(*args, **kwargs)
            if inspect.iscoroutine(out):
                raise TypeError(
                    "input_transform returned a coroutine but wrapped "
                    "function is sync; use async decorated function"
                )
            return out  # type: ignore[return-value]

        # Sync generator — frame mode: stay pure sync.
        if func_sync_gen and output_mode != "generator":

            @wraps(func)
            def sync_gen_wrapper(*args: Any, **kwargs: Any) -> Any:
                new_args, new_kwargs = _run_input_transform_sync(
                    args, kwargs
                )
                call_args, call_kwargs = _bind_args_no_duplicate(
                    func, new_args, new_kwargs
                )
                gen = func(*call_args, **call_kwargs)
                if not inspect.isgenerator(gen):
                    raise TypeError(
                        "expected generator function, got " f"{type(gen)}"
                    )
                for item in gen:
                    if output_transform is None:
                        yield item
                    else:
                        out = output_transform(item)
                        if inspect.iscoroutine(out):
                            raise TypeError(
                                "output_transform returned a coroutine but "
                                "wrapped function is sync; use async "
                                "decorated function for async transform"
                            )
                        yield out

            return sync_gen_wrapper  # type: ignore[return-value]

        # Sync plain function: stay pure sync.
        if not func_async and not func_async_gen and not func_sync_gen:

            @wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                new_args, new_kwargs = _run_input_transform_sync(args, kwargs)
                call_args, call_kwargs = _bind_args_no_duplicate(
                    func, new_args, new_kwargs
                )
                result = func(*call_args, **call_kwargs)
                if output_transform is None:
                    return result
                out = output_transform(result)
                if inspect.iscoroutine(out):
                    raise TypeError(
                        "output_transform returned a coroutine but wrapped "
                        "function is sync; use async decorated function"
                    )
                return out

            return sync_wrapper

        # --- async paths: delegate to _make_transform_io_decorator ---

        async def _direct_input_fn(
            args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> tuple[tuple[Any, ...], dict[str, Any]]:
            if input_transform is None:
                return args, kwargs
            out = input_transform(*args, **kwargs)
            if inspect.iscoroutine(out):
                return await out
            return out  # type: ignore[return-value]

        async def _direct_output_fn(value: Any) -> Any:
            if output_transform is None:
                return value
            return await _apply_output_transform(value, output_transform)

        output_source_fn: Optional[Callable] = None
        if output_mode == "generator" and output_transform is not None:

            async def _direct_output_source(source: Any) -> Any:
                return output_transform(source)

            output_source_fn = _direct_output_source

        return _make_transform_io_decorator(
            func,
            input_fn=_direct_input_fn,
            output_fn=_direct_output_fn,
            output_source_fn=output_source_fn,
            output_mode=output_mode,
        )

    return decorator


def create_transform_io_by_events_decorator(
    framework: Any,
    *,
    input_event: Optional[str] = None,
    output_event: Optional[str] = None,
    result_key: str = "result",
    output_mode: Literal["frame", "generator"] = "frame",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Create transform_io decorator driven by event callbacks.

    Before calling the wrapped function, triggers input_event with
    (*args, **kwargs); the last callback return value is used as
    (new_args, new_kwargs). After the function returns (or for each yielded
    item), triggers output_event; the last callback return value is used as
    the transformed output. Callbacks must be async.

    ``output_mode`` controls how output_event is triggered for generator
    functions:

    * ``'frame'`` (default): output_event is triggered once per yielded item.
      Callbacks receive ``result_key=<item>`` and return the transformed item.
    * ``'generator'``: output_event is triggered once with the entire source
      async iterator as ``result_key=<source>``. Callbacks should return an
      async iterable. If no callbacks are registered, the source is passed
      through unchanged. Sync generator functions are promoted to async.

    Args:
        framework: AsyncCallbackFramework instance (has trigger_transform).
        input_event: Event name for input transform. Callbacks receive
            (*args, **kwargs) and must return (new_args, new_kwargs).
        output_event: Event name for output transform. In ``'frame'`` mode:
            callbacks receive result_key=<item> and return transformed item.
            In ``'generator'`` mode: callbacks receive result_key=<source>
            and return an async iterable.
        result_key: Keyword used when triggering output_event (default:
            "result").
        output_mode: ``'frame'`` (default) or ``'generator'``. Only affects
            generator functions; raises ``ValueError`` for non-generators.

    Returns:
        A decorator that uses event callbacks for input/output transform.
    """

    async def _input_from_events(
        args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        if not input_event:
            return args, kwargs
        result = await framework.trigger_transform(input_event, *args, **kwargs)
        if result is _TRANSFORM_NOOP:
            return args, kwargs
        if isinstance(result, (tuple, list)) and len(result) == 2:
            return (tuple(result[0]), dict(result[1]))
        return args, kwargs

    async def _output_from_events(value: Any) -> Any:
        if not output_event:
            return value
        result = await framework.trigger_transform(
            output_event, **{result_key: value}
        )
        if result is _TRANSFORM_NOOP:
            return value
        return result

    output_source_fn: Optional[Callable] = None
    if output_event:

        async def _output_source_from_events(source: Any) -> Any:
            transformed = await framework.trigger_transform(
                output_event, **{result_key: source}
            )
            return source if transformed is _TRANSFORM_NOOP else transformed

        output_source_fn = _output_source_from_events

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return _make_transform_io_decorator(
            func,
            input_fn=_input_from_events,
            output_fn=_output_from_events,
            output_source_fn=output_source_fn,
            output_mode=output_mode,
        )

    return decorator


# ---------------------------------------------------------------------------
# Wrap handler infrastructure
# ---------------------------------------------------------------------------

# Event-name prefix used when storing WrapHandlers inside the framework's
# standard _callbacks registry.  Keeps wrap entries isolated from regular
# event callbacks while reusing the same priority-sorted CallbackInfo store.
_WRAP_EVENT_PREFIX = "__wrap__:"


def _get_framework_wrap_handlers(
    framework: Any, event: str
) -> list[WrapHandler]:
    """Return enabled WrapHandlers registered on the framework for *event*.

    Handlers are stored in ``framework.callbacks`` under the key
    ``_WRAP_EVENT_PREFIX + event``, ordered by descending priority (the same
    sorting that ``register_sync`` maintains for all callbacks).
    """
    infos = framework.callbacks.get(f"{_WRAP_EVENT_PREFIX}{event}", [])
    return [info.callback for info in infos if info.enabled]


def _build_wrap_chain_regular(
    innermost: Callable, handlers: list[WrapHandler]
) -> Callable:
    """Build a regular (non-generator) async WrapHandler chain.

    Returns an async callable equivalent to running handlers[0] around
    handlers[1] around … around *innermost*.
    """
    chain: Callable = innermost
    for h in reversed(handlers):
        prev = chain

        def _make_link(handler: WrapHandler, n: Callable) -> Callable:
            async def link(*a: Any, **kw: Any) -> Any:
                return await handler(n, *a, **kw)

            return link

        chain = _make_link(h, prev)
    return chain


def _build_wrap_chain_gen(
    innermost: Callable, handlers: list[WrapHandler]
) -> Callable:
    """Build an async-generator WrapHandler chain.

    Returns an async generator callable whose items come from running
    handlers[0] around handlers[1] around … around *innermost*.
    Each handler must be an async generator function that iterates
    ``call_next(*args, **kwargs)`` and yields (optionally transformed) items.
    """
    chain: Callable = innermost
    for h in reversed(handlers):
        prev = chain

        def _make_gen_link(handler: WrapHandler, n: Callable) -> Callable:
            async def gen_link(*a: Any, **kw: Any) -> Any:
                async for item in handler(n, *a, **kw):
                    yield item

            return gen_link

        chain = _make_gen_link(h, prev)
    return chain


# ---------------------------------------------------------------------------
# Public decorator factories
# ---------------------------------------------------------------------------


def _make_wrap_decorator(
    func: Callable[..., Any],
    get_handlers: Callable[[], list[WrapHandler]],
) -> Callable[..., Any]:
    """Build a wrapped version of *func* using a WrapHandler chain.

    *get_handlers* is called on **every invocation** to obtain the current
    handler list.  This is the single shared core for both static chains
    (``create_wrap_decorator``) and dynamic/event-based chains
    (``create_wrap_by_event_decorator``).

    Args:
        func: The function to wrap.
        get_handlers: Zero-argument callable that returns the ordered
            WrapHandler list (outermost first) each time the wrapped function
            is called.

    Returns:
        Wrapped async function or async generator, preserving *func*'s
        metadata via ``functools.wraps``.
    """
    func_async_gen = inspect.isasyncgenfunction(func)
    func_sync_gen = inspect.isgeneratorfunction(func)
    func_async = inspect.iscoroutinefunction(func)

    # --- generator path -------------------------------------------------------
    if func_async_gen or func_sync_gen:
        if func_async_gen:

            async def _inner_gen(*a: Any, **kw: Any) -> Any:
                async for item in func(*a, **kw):
                    yield item

        else:

            async def _inner_gen(*a: Any, **kw: Any) -> Any:  # type: ignore[misc]
                for item in func(*a, **kw):
                    yield item

        @wraps(func)
        async def gen_wrapper(*args: Any, **kwargs: Any) -> Any:
            handlers = get_handlers()
            if not handlers:
                async for item in _inner_gen(*args, **kwargs):
                    yield item
                return
            async for item in _build_wrap_chain_gen(_inner_gen, handlers)(*args, **kwargs):
                yield item

        return gen_wrapper  # type: ignore[return-value]

    # --- regular function path ------------------------------------------------
    if func_async:
        _inner: Callable = func
    else:

        async def _sync_as_async(*a: Any, **kw: Any) -> Any:
            return func(*a, **kw)

        _inner = _sync_as_async

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        handlers = get_handlers()
        if not handlers:
            return await _inner(*args, **kwargs)
        return await _build_wrap_chain_regular(_inner, handlers)(*args, **kwargs)

    return wrapper


def create_wrap_decorator(
    *handlers: WrapHandler,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Create decorator that applies a static WrapHandler chain around a function.

    Each handler is an async callable that receives ``call_next`` as its first
    argument and decides when (and whether) to invoke the rest of the chain::

        async def handler(call_next, *args, **kwargs):
            # pre-processing
            result = await call_next(*args, **kwargs)
            # post-processing
            return result

    Handlers are applied in the given order: ``handlers[0]`` is the outermost
    (called first); the original function is the innermost.

    For async/sync generators, each handler must be an **async generator
    function** that iterates ``call_next`` and yields items::

        async def handler(call_next, *args, **kwargs):
            async for item in call_next(*args, **kwargs):
                yield transform(item)

    Supports: async functions, sync functions (promoted to async), async
    generators, and sync generators (promoted to async generators).

    Example::

        async def log_handler(call_next, *args, **kwargs):
            print("before")
            result = await call_next(*args, **kwargs)
            print("after")
            return result

        @create_wrap_decorator(log_handler)
        async def add(a, b):
            return a + b

    Args:
        *handlers: WrapHandler callables in outermost-first order.

    Returns:
        A decorator that wraps the function with the handler chain.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if not handlers:
            return func
        handler_list = list(handlers)
        return _make_wrap_decorator(func, lambda: handler_list)

    return decorator


def create_on_wrap_decorator(
    framework: Any,
    event: str,
    *,
    priority: int = 0,
) -> Callable[[WrapHandler], WrapHandler]:
    """Create decorator that registers a WrapHandler for the given event.

    The handler is stored in the framework's standard ``_callbacks`` store
    under the key ``"__wrap__:{event}"``, so it participates in the same
    priority-sorted registry as regular event callbacks and can be unregistered
    via the framework's existing ``unregister`` / ``unregister_event`` API.

    For regular functions the handler signature is::

        async def handler(call_next, *args, **kwargs):
            result = await call_next(*args, **kwargs)
            return result

    For generator-wrapping events the handler must be an async generator::

        async def handler(call_next, *args, **kwargs):
            async for item in call_next(*args, **kwargs):
                yield transformed(item)

    Args:
        framework: AsyncCallbackFramework instance.
        event: Logical event name (the same name used in ``wrap()``).
        priority: Higher-priority handlers are outermost in the chain.

    Returns:
        A decorator that registers the function and returns it unchanged.
    """

    def decorator(func: WrapHandler) -> WrapHandler:
        framework.register_sync(
            f"{_WRAP_EVENT_PREFIX}{event}",
            func,
            priority=priority,
        )
        return func

    return decorator


def create_wrap_by_event_decorator(
    framework: Any,
    event: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Create decorator that wraps a function with event-registered WrapHandlers.

    At each invocation, handlers are retrieved from ``framework.callbacks``
    under ``"__wrap__:{event}"``, so handlers registered after decoration are
    automatically included and disabled handlers are skipped.  Register
    handlers with :func:`create_on_wrap_decorator` (or ``framework.on_wrap``).

    The chain ordering and handler signatures are identical to
    :func:`create_wrap_decorator`.

    Args:
        framework: AsyncCallbackFramework instance.
        event: Logical event name (the same name used in ``on_wrap()``).

    Returns:
        A decorator that wraps the function with the dynamic handler chain.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return _make_wrap_decorator(
            func,
            lambda: _get_framework_wrap_handlers(framework, event),
        )

    return decorator
