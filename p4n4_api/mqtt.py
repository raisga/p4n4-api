"""MQTT: one persistent connection that publishes ingested readings and feeds live streams.

A background task (started with the app) connects, subscribes to `sensors/+/+` and hands
each reading to the live-stream subscribers; it reconnects with backoff when the broker
goes away. Readings the API ingests are published to the same topics, so they reach
subscribers the same way as readings devices publish themselves.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import aiomqtt

log = logging.getLogger("p4n4_api.mqtt")

TOPIC_FILTER = "sensors/+/+"
# Marks readings the API already stored, so the IoT stack's Node-RED flow skips them
# instead of writing them to InfluxDB a second time.
STORED_MARKER = "_stored_by"
STORED_BY = "p4n4-api"
# Payload keys that aren't readings: identity comes from the topic (Node-RED ignores
# device/model too), and ts/_stored_by are added by the API.
RESERVED_KEYS = frozenset({"device", "model", "ts", STORED_MARKER})
QUEUE_SIZE = 1000


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(eq=False)  # compared (and kept in a set) by identity
class Subscriber:
    device: str | None
    sensor: str | None
    queue: asyncio.Queue[dict] = field(default_factory=lambda: asyncio.Queue(QUEUE_SIZE))
    dropped: int = 0

    def offer(self, reading: dict) -> None:
        if (self.device and reading["device"] != self.device) or (
            self.sensor and reading["sensor"] != self.sensor
        ):
            return
        if self.queue.full():  # a slow client loses its oldest readings, nobody else waits
            self.queue.get_nowait()
            self.dropped += 1
        self.queue.put_nowait(reading)


def reading_from(topic: str, payload: bytes) -> dict | None:
    """A stream event from a `sensors/{device}/{sensor}` message, or None if malformed."""
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "sensors" or not parts[1] or not parts[2]:
        return None
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    fields = {k: v for k, v in data.items() if k not in RESERVED_KEYS}
    received = _now()
    ts = data.get("ts") if data.get(STORED_MARKER) == STORED_BY else None
    return {
        "device": parts[1],
        "sensor": parts[2],
        "fields": fields,
        "ts": ts or received,
        "received_at": received,
    }


class Unavailable(Exception):
    """Not connected to the broker."""


PUBLISH_TIMEOUT_S = 10


class Bridge:
    def __init__(self) -> None:
        self._client: aiomqtt.Client | None = None
        self._task: asyncio.Task | None = None
        self._subscribers: set[Subscriber] = set()
        self.error: str | None = "not started"

    @property
    def connected(self) -> bool:
        return self._client is not None

    def start(
        self, host: str, port: int, username: str | None = None, password: str | None = None
    ) -> None:
        self._task = asyncio.create_task(self._run(host, port, username, password))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._client = None
        self.error = "not started"

    async def _run(self, host: str, port: int, username: str | None, password: str | None):
        delay = 1.0
        while True:
            try:
                async with aiomqtt.Client(
                    host, port, username=username, password=password, identifier=None
                ) as client:
                    await client.subscribe(TOPIC_FILTER)
                    self._client, self.error, delay = client, None, 1.0
                    log.info("MQTT connected to %s:%s", host, port)
                    async for message in client.messages:
                        reading = reading_from(message.topic.value, message.payload)
                        if reading is not None:
                            self.dispatch(reading)
            except aiomqtt.MqttError as exc:
                if self.error != str(exc):  # log state changes, not every retry
                    log.warning("MQTT unavailable (%s:%s): %s; retrying", host, port, exc)
                self.error = str(exc) or "connection failed"
            finally:
                self._client = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    def dispatch(self, reading: dict) -> None:
        for subscriber in list(self._subscribers):
            subscriber.offer(reading)

    async def publish(self, device: str, sensor: str, fields: dict, ts: str) -> bool:
        """Publish a stored reading, marked so Node-RED doesn't store it again. When the
        broker is down, live-stream subscribers get it directly instead."""
        payload = {**fields, "ts": ts, STORED_MARKER: STORED_BY}
        client = self._client
        if client is not None:
            try:
                await client.publish(f"sensors/{device}/{sensor}", json.dumps(payload), qos=1)
                return True
            except aiomqtt.MqttError as exc:
                log.warning("MQTT publish failed: %s", exc)
        now = _now()
        self.dispatch(
            {"device": device, "sensor": sensor, "fields": fields, "ts": ts, "received_at": now}
        )
        return False

    async def send(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> None:
        """Publish as-is. With QoS 1 or 2 this returns once the broker acknowledged it.
        Raises Unavailable when disconnected, aiomqtt.MqttError when the publish fails."""
        client = self._client
        if client is None:
            raise Unavailable(self.error or "not connected")
        await client.publish(topic, payload, qos=qos, retain=retain, timeout=PUBLISH_TIMEOUT_S)

    @contextlib.asynccontextmanager
    async def subscribe(
        self, device: str | None = None, sensor: str | None = None
    ) -> AsyncIterator[Subscriber]:
        subscriber = Subscriber(device, sensor)
        self._subscribers.add(subscriber)
        try:
            yield subscriber
        finally:
            self._subscribers.discard(subscriber)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bridge = Bridge()
