"""Publish MQTT messages (operator+), e.g. commands to devices, within the allowed topics."""

from __future__ import annotations

import json
from typing import Any, Literal

import aiomqtt
from fastapi import APIRouter
from pydantic import BaseModel, field_validator

from p4n4_api import audit, topics
from p4n4_api.auth import CurrentUser
from p4n4_api.config import load_settings
from p4n4_api.errors import ApiError
from p4n4_api.mqtt import Unavailable, bridge

router = APIRouter(prefix="/mqtt", tags=["mqtt"])

MAX_PAYLOAD_BYTES = 256 * 1024


class Publish(BaseModel):
    topic: str
    # A string is sent as-is; anything else (object, array, number, bool, null) as JSON.
    # With retain, an empty string clears the topic's retained message.
    payload: Any = ""
    qos: Literal[0, 1, 2] = 0
    retain: bool = False

    @field_validator("topic")
    @classmethod
    def _topic(cls, topic: str) -> str:
        return topics.check_topic(topic)


class Published(BaseModel):
    topic: str
    qos: int
    retain: bool
    bytes: int


def _encode(payload: Any) -> bytes:
    return payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()


def _allowed(topic: str) -> bool:
    settings = load_settings()
    if any(topics.matches(f, topic) for f in settings.mqtt_publish_deny):
        return False
    return any(topics.matches(f, topic) for f in settings.mqtt_publish_allow)


@router.post("/publish")
async def publish(body: Publish, user: CurrentUser) -> Published:
    """Publish one message. With `qos` 1 or 2, success means the broker acknowledged it.

    Topics are limited by P4N4_API_MQTT_PUBLISH_ALLOW / _DENY (by default everything but
    `sensors/#` and `inference/#`: readings come from devices, not people). Each publish
    is in the audit log.
    """
    if not _allowed(body.topic):
        raise ApiError(403, "topic_not_allowed", f"Publishing to {body.topic!r} isn't allowed.")
    data = _encode(body.payload)
    if len(data) > MAX_PAYLOAD_BYTES:
        raise ApiError(
            413, "payload_too_large", f"Payload must be at most {MAX_PAYLOAD_BYTES} bytes."
        )
    try:
        await bridge.send(body.topic, data, body.qos, body.retain)
    except Unavailable as exc:
        raise ApiError(503, "mqtt_unavailable", f"Not connected to the MQTT broker: {exc}") from exc
    except aiomqtt.MqttError as exc:
        raise ApiError(502, "upstream_error", f"MQTT publish failed: {exc}") from exc
    flags = f"qos {body.qos}, {len(data)} bytes" + (", retained" if body.retain else "")
    audit.record(user.username, "mqtt.publish", body.topic, flags)
    return Published(topic=body.topic, qos=body.qos, retain=body.retain, bytes=len(data))
