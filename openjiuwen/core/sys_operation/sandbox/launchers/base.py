from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from openjiuwen.core.sys_operation.config import SandboxLauncherConfig


@dataclass(frozen=True)
class LaunchedSandbox:
    """Descriptor returned by :meth:`SandboxLauncher.launch`.

    This is the durable handle used by :class:`ContainerManager` to identify
    a running sandbox instance across pause / resume / delete calls.

    Attributes:
        base_url:     HTTP base URL for the sandbox service (empty string for
                      provider-managed sandboxes like E2B).
        sandbox_id:   Opaque identifier assigned by the runtime (Docker
                      container id, E2B sandbox id, etc.).  May be ``None``
                      for remote launchers where lifecycle is external.
        host_port:    Host-side mapped port (Docker only, ``None`` otherwise).
    """

    base_url: str
    sandbox_id: Optional[str] = None
    host_port: Optional[int] = None


class SandboxLauncher:
    """Base class for sandbox lifecycle management.

    All methods except :meth:`launch` are no-ops in this base class so that
    implementors only override what their runtime actually supports.

    **Important**: :meth:`launch` is the *only* entry that returns a
    :class:`LaunchedSandbox` descriptor.  ``pause``, ``resume``, and
    ``delete`` all operate on that descriptor but do not create new ones.
    The caller (ContainerManager) is responsible for deciding *which*
    operation to invoke and persisting the descriptor.
    """

    async def launch(
            self,
            config: SandboxLauncherConfig,
            timeout_seconds: int,
            isolation_key: Optional[str] = None,
    ) -> LaunchedSandbox:
        """Start (or resume) a sandbox and return its descriptor.

        Implementations are strongly encouraged to use *sandbox_id* as the container
        name / label so that a paused sandbox can be found and unpaused on the
        next ``launch()`` call instead of being re-created.
        """
        raise NotImplementedError

    async def pause(self, sandbox_id: str) -> None:
        """Suspend the sandbox to preserve state without consuming compute."""
        return None

    async def resume(self, sandbox_id: str) -> None:
        """Resume a previously paused sandbox."""
        return None

    async def delete(self, sandbox_id: str) -> None:
        """Permanently destroy the sandbox and release its resources."""
        return None

    async def check_status(self, sandbox_id: str) -> "SandboxStatus":
        """Check the current status of the sandbox."""
        from openjiuwen.core.sys_operation.sandbox.gateway.sandbox_store import SandboxStatus
        return SandboxStatus.RUNNING
