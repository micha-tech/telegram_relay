"""Queue of signal source backfill requests consumed by the Telegram worker.

The worker owns the Telegram session for a connected account, so backfills are
requested through Redis and executed by the worker with its existing client.
That avoids two clients using the same authentication key at the same time.
"""

SCRAPE_QUEUE_PREFIX = "relay:scrape:"


def scrape_queue_key(account_id) -> str:
    return f"{SCRAPE_QUEUE_PREFIX}{account_id}"


def enqueue_scrape(account_id, source_id) -> int:
    """Queue a backfill for one signal source; returns the queue length."""
    from .redis import RedisService

    client = RedisService().client
    return client.rpush(scrape_queue_key(account_id), str(source_id))
