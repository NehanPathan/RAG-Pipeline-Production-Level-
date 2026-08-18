"""Queue health, refreshed at scrape time.

A worker killed mid-job leaves its row `running` forever: nothing
transitions it, because the thing that would have is gone. Nothing else in
the system notices, and the document sits in `processing` with a user
waiting on it.

Refreshed when Prometheus scrapes rather than on a timer, so the number is
never staler than the scrape interval and there is no separate scheduler to
forget to run. It is one indexed count.
"""

from __future__ import annotations

from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import documents_stuck

logger = get_logger(__name__)


async def refresh_queue_gauges() -> None:
    """Publish the stuck-job count. Best-effort: a metrics scrape must not
    fail because the database is briefly unavailable."""
    from src.api.dependencies import get_job_repository
    from src.config import get_settings

    try:
        count = await get_job_repository().count_stuck(
            older_than_seconds=get_settings().job_stuck_after_seconds
        )
    except Exception as exc:
        logger.debug("stuck_job_gauge_refresh_failed", error=str(exc))
        return
    documents_stuck.set(count)
