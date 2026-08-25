"""Redis client and its readiness probe.

Redis holds the JWT denylist and, in ``booking``, the availability cache. It is
a dependency of readiness even though correctness never depends on it (D25):
losing it costs latency and, here, the ability to revoke a token — enough to
stop taking traffic, not enough to fail liveness and be restarted.
"""

from __future__ import annotations

from redis.asyncio import Redis

from identity.application.readiness import DependencyStatus


def create_client(dsn: str) -> Redis:
    return Redis.from_url(
        dsn,
        # Every external call has a timeout (CODING_STANDARDS 8). Without one,
        # an unreachable Redis turns into a hung request rather than an error.
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
        decode_responses=True,
    )


class CacheProbe:
    """Readiness probe for Redis."""

    def __init__(self, client: Redis) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "redis"

    async def check(self) -> DependencyStatus:
        await self._client.ping()
        return DependencyStatus(name=self.name, is_ready=True)
