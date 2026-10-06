import os
from contextlib import contextmanager
from redis import Redis

class RedisService:
    def __init__(self, client=None):
        self.client = client or Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    @contextmanager
    def lock(self, name: str, timeout: int = 30):
        lock = self.client.lock(f"relay:lock:{name}", timeout=timeout, blocking_timeout=5)
        if not lock.acquire(): raise RuntimeError("Resource is busy")
        try: yield
        finally: lock.release()
    def health(self): return bool(self.client.ping())

