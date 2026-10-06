"""Fan WebSocket events out to every backend worker.

Each worker keeps its own WebSocket connections in memory (``websocket.manager`` and
``notification_manager``). With several workers or containers, an event raised in one
worker must reach sockets held by the others, so events go through Redis pub/sub when
``REDIS_URL`` is set: every worker publishes to one channel and every worker's listener
delivers the event to its local sockets. Without Redis, events are delivered locally.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

from app.core.notification_manager import notification_manager
from app.core.websocket import manager

logger = logging.getLogger(__name__)

CHANNEL = "smartcity:events"


async def deliver(event: dict) -> None:
    """Send one event to the sockets connected to this worker."""
    target = event.get("target")
    message = event.get("message", {})
    if target == "complaints":
        await manager.broadcast(message)
    elif target == "notifications":
        await notification_manager.broadcast(message)
    elif target == "user":
        await notification_manager.send_to_user(int(event["user_id"]), message)
    elif target == "disconnect_user":
        # Access revoked: close every live connection the user holds on this worker.
        await manager.disconnect_user(int(event["user_id"]))
        await notification_manager.close_user(int(event["user_id"]))
    else:
        logger.warning("Dropping event with unknown target %r", target)


class EventBus:
    def __init__(self) -> None:
        self._redis = None
        self._listener: asyncio.Task | None = None

    @property
    def distributed(self) -> bool:
        return self._redis is not None

    async def start(self, redis_url: str | None, client_factory: Callable | None = None) -> None:
        if not redis_url and client_factory is None:
            logger.info("Event bus: in-process (set REDIS_URL to share events across workers)")
            return
        try:
            if client_factory is None:
                import redis.asyncio as redis

                client_factory = lambda: redis.from_url(redis_url, decode_responses=True)  # noqa: E731
            self._redis = client_factory()
            await self._redis.ping()
        except Exception as exc:
            logger.error("Event bus: Redis unavailable (%s); falling back to in-process delivery", exc)
            self._redis = None
            return
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(CHANNEL)
        self._listener = asyncio.create_task(self._listen(pubsub))
        logger.info("Event bus: Redis pub/sub on channel %s", CHANNEL)

    async def _listen(self, pubsub) -> None:
        while True:
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("type") == "message":
                    await deliver(json.loads(message["data"]))
            except asyncio.CancelledError:
                await pubsub.aclose() if hasattr(pubsub, "aclose") else await pubsub.close()
                raise
            except Exception as exc:  # keep listening through malformed events / transient errors
                logger.warning("Event bus listener error: %s", exc)
                await asyncio.sleep(1)

    async def publish(self, event: dict) -> None:
        if self._redis is not None:
            try:
                await self._redis.publish(CHANNEL, json.dumps(event))
                return
            except Exception as exc:
                logger.error("Event bus: publish failed (%s); delivering locally only", exc)
        await deliver(event)

    async def stop(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            try:
                await self._listener
            except asyncio.CancelledError:
                pass
            self._listener = None
        if self._redis is not None:
            await self._redis.aclose() if hasattr(self._redis, "aclose") else await self._redis.close()
            self._redis = None


event_bus = EventBus()
