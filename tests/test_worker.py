import asyncio
import uuid

import pytest

from relay.services.worker import TelegramRelayWorker

pytestmark = pytest.mark.django_db


def make_worker(monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "test-api-hash")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    class FakeRedis:
        def __init__(self, *args, **kwargs):
            pass

        @classmethod
        def from_url(cls, *args, **kwargs):
            return cls()

        def lock(self, *args, **kwargs):
            raise AssertionError("lock should not be used in unit tests")

        async def aclose(self):
            pass

    monkeypatch.setattr("relay.services.worker.Redis", FakeRedis)
    return TelegramRelayWorker()


def test_backoff_grows_exponentially_and_resets(monkeypatch, telegram_env):
    worker = make_worker(monkeypatch)
    account_id = uuid.uuid4()

    async def scenario():
        loop = asyncio.get_running_loop()
        worker._record_failure(account_id, "OSError")
        first = worker.backoff_until[account_id] - loop.time()
        worker._record_failure(account_id, "OSError")
        second = worker.backoff_until[account_id] - loop.time()
        assert 1 <= first <= 2
        assert 2 <= second <= 4
        assert worker.failures[account_id] == 2
        worker._record_success(account_id)
        assert account_id not in worker.failures
        assert account_id not in worker.backoff_until

    asyncio.run(scenario())


def test_backoff_is_capped(monkeypatch, telegram_env):
    worker = make_worker(monkeypatch)
    worker.max_backoff_seconds = 10

    async def scenario():
        loop = asyncio.get_running_loop()
        for _ in range(10):
            worker._record_failure(uuid.uuid4(), "OSError")
        for deadline in worker.backoff_until.values():
            assert deadline - loop.time() <= 10

    asyncio.run(scenario())


def test_reconcile_skips_accounts_in_backoff(monkeypatch, telegram_env):
    worker = make_worker(monkeypatch)
    worker.poll_interval_seconds = 999
    account_id = uuid.uuid4()

    class FakeQS:
        def values_list(self, *args, **kwargs):
            return self

        def __aiter__(self):
            async def gen():
                yield account_id

            return gen()

    class FakeManager:
        def filter(self, **kwargs):
            return FakeQS()

    monkeypatch.setattr("relay.services.worker.TelegramAccount.objects", FakeManager())

    async def scenario():
        loop = asyncio.get_running_loop()
        worker.backoff_until[account_id] = loop.time() + 60
        await worker._reconcile_accounts()
        assert worker.account_tasks == {}
        worker.backoff_until[account_id] = loop.time() - 1
        await worker._reconcile_accounts()
        assert account_id in worker.account_tasks
        for task in worker.account_tasks.values():
            task.cancel()
        await asyncio.gather(*worker.account_tasks.values(), return_exceptions=True)

    asyncio.run(scenario())
