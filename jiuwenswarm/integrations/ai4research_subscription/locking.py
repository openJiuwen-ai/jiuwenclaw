"""Cross-process exclusion for authentication and model turns."""

from __future__ import annotations

import asyncio

import portalocker

from .constants import PROFILE_LOCK_POLL_SECONDS, PROFILE_LOCK_TIMEOUT_SECONDS
from .errors import CodexProviderError
from .profiles import CodexProfile


def acquire_profile_lock(profile: CodexProfile):
    """Attempt the profile lock once without sleeping or blocking the event loop."""

    lock = portalocker.Lock(
        str(profile.lock_path),
        mode="a+",
        timeout=0,
        flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        fail_when_locked=True,
    )
    try:
        return lock.acquire()
    except portalocker.exceptions.LockException as exc:
        raise CodexProviderError("provider_busy", "Codex is already handling another operation.") from exc


async def acquire_profile_lock_async(profile: CodexProfile):
    """Poll immediate lock attempts within the provider's monotonic deadline."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + PROFILE_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            return acquire_profile_lock(profile)
        except CodexProviderError as exc:
            if exc.code != "provider_busy":
                raise
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise
            await asyncio.sleep(min(PROFILE_LOCK_POLL_SECONDS, remaining))


def release_profile_lock(handle) -> None:
    try:
        portalocker.unlock(handle)
    finally:
        handle.close()
