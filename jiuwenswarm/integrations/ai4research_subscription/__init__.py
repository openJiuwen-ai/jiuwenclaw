"""AI4Research subscription-backed model-provider constants.

Import ``model_client`` explicitly in the AgentServer process to register the
native OpenJiuwen client.  Keeping this package initializer inert prevents
Gateway capability/config imports from becoming data-plane registration.
"""

from .constants import CODEX_MODEL_ALIAS, CODEX_PROVIDER_NAME

__all__ = [
    "CODEX_MODEL_ALIAS",
    "CODEX_PROVIDER_NAME",
]
