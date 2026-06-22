"""Cron job scheduling for Gateway.

This package provides:
- CronJob models and JSON persistence (cron_jobs.json in user workspace)
- An asyncio scheduler that wakes the agent before push time and pushes results to channels

Heavy modules (CronSchedulerService, CronController) are lazily imported
to avoid pulling the entire gateway chain during AgentServer startup.
"""

import sys

from jiuwenclaw._lazy import install_lazy_attrs

from .models import CronJob, CronTarget, CronTargetChannel
from .store import CronJobStore

__all__ = [
    "CronJob",
    "CronTarget",
    "CronTargetChannel",
    "CronJobStore",
]

install_lazy_attrs(sys.modules[__name__], {
    "CronSchedulerService": (".scheduler", "CronSchedulerService"),
    "CronController": (".controller", "CronController"),
})
