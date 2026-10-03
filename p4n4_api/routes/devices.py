"""Device registry: operators can read it, admins register, change and remove devices."""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from p4n4_api import audit, auth, db, devices
from p4n4_api.auth import CurrentUser
from p4n4_api.errors import ApiError

router = APIRouter(prefix="/devices", tags=["devices"])
admin_only = [Depends(auth.require_role("admin"))]


class Device(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    # Start of the current API key (`p4n4_<key id>`), to tell keys apart; not usable as one
    key_prefix: str
    created_at: str
    # Last time the device exchanged its API key for a token
    last_seen_at: str | None


class DevicePage(BaseModel):
    items: list[Device]
    total: int
    limit: int
    offset: int


class DeviceCreate(BaseModel):
    id: str
    name: str = ""
    description: str = ""


class DeviceUpdate(BaseModel):
    """Fields to change; omitted ones stay as they are."""

    name: str | None = None
    description: str | None = None
    # Disabled devices can't sign in, and their current tokens stop working.
    enabled: bool | None = None


class DeviceWithKey(Device):
    # Shown once: only a hash is stored. POST /devices/{id}/key makes a new one.
    api_key: str


class NewKey(BaseModel):
    id: str
    key_prefix: str
    api_key: str


def _existing(conn: sqlite3.Connection, device_id: str) -> str:
    """The normalized device ID, or 404."""
    try:
        normalized = devices.normalize_id(device_id)
    except devices.DeviceError:
        normalized = None
    if normalized is None or devices.get(conn, normalized) is None:
        raise HTTPException(status_code=404, detail=f"No device '{device_id}'.")
    return normalized


@router.get("")
def list_devices(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DevicePage:
    """Devices ordered by ID, a page at a time."""
    with db.connect() as conn:
        items, total = devices.list_page(conn, limit, offset)
    return DevicePage(items=items, total=total, limit=limit, offset=offset)


@router.post("", status_code=201, dependencies=admin_only)
def create_device(body: DeviceCreate, actor: CurrentUser) -> DeviceWithKey:
    """Register a device. The response holds its API key, which isn't shown again."""
    with db.connect() as conn:
        try:
            device, key = devices.create(conn, body.id, body.name, body.description)
        except devices.DeviceExists as exc:
            raise ApiError(409, "device_exists", str(exc)) from exc
        except devices.DeviceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit.record(actor.username, "device.create", device["id"], conn=conn)
    return DeviceWithKey(**device, api_key=key)


@router.get("/{device_id}")
def get_device(device_id: str) -> Device:
    with db.connect() as conn:
        return Device(**devices.get(conn, _existing(conn, device_id)))


@router.patch("/{device_id}", dependencies=admin_only)
def update_device(device_id: str, body: DeviceUpdate, actor: CurrentUser) -> Device:
    with db.connect() as conn:
        normalized = _existing(conn, device_id)
        devices.update(
            conn, normalized, name=body.name, description=body.description, enabled=body.enabled
        )
        changed = ", ".join(sorted(body.model_dump(exclude_none=True)))
        audit.record(actor.username, "device.update", normalized, changed, conn)
        return Device(**devices.get(conn, normalized))


@router.delete("/{device_id}", status_code=204, dependencies=admin_only)
def delete_device(device_id: str, actor: CurrentUser) -> Response:
    """Deregister a device. Its key and tokens stop working at once."""
    with db.connect() as conn:
        normalized = _existing(conn, device_id)
        devices.delete(conn, normalized)
        audit.record(actor.username, "device.delete", normalized, conn=conn)
    return Response(status_code=204)


@router.post("/{device_id}/key", dependencies=admin_only)
def rotate_key(device_id: str, actor: CurrentUser) -> NewKey:
    """Replace the device's API key. The old key and its tokens stop working at once; the
    new key is shown only in this response."""
    with db.connect() as conn:
        normalized = _existing(conn, device_id)
        key = devices.rotate_key(conn, normalized)
        audit.record(actor.username, "device.rotate_key", normalized, conn=conn)
        return NewKey(
            id=normalized, key_prefix=devices.get(conn, normalized)["key_prefix"], api_key=key
        )
