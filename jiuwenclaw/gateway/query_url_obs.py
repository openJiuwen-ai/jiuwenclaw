"""Placeholder OBS download URL refresh — override at deployment."""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


class QueryUrlOSMS:
    """Resolve a fresh OBS download URL from an expired or stale URL via OSMS HTTP API."""

    def __init__(self) -> None:
        self.trace_id = str(uuid.uuid4())[:16]

    async def get_latest_obs_url(self, old_url: str) -> str:
        """Return the latest downloadable URL for the given OBS URL.

        Deployment should replace this module with an implementation that
        calls the OSMS HTTP API to refresh signed URLs before they expire.
        """
        url = str(old_url or "").strip()
        if not url:
            return url
        logger.debug(
            "QueryUrlOSMS placeholder: returning original url (trace_id=%s)",
            self.trace_id,
        )
        return url
