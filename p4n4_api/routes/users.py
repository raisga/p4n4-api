"""User management for admins: create client accounts without a shell on the server."""

from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from p4n4_api import audit, db, users
from p4n4_api.auth import CurrentUser
from p4n4_api.errors import ApiError

router = APIRouter(prefix="/users", tags=["users"])

Role = Literal["operator", "admin"]


class UserOut(BaseModel):
    username: str
    role: str
    created_at: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: Role = "operator"


class UserUpdate(BaseModel):
    """Fields to change; omitted ones stay as they are."""

    role: Role | None = None
    # Resetting someone's password signs them out everywhere.
    password: str | None = None


def _error(exc: users.UserError) -> HTTPException:
    if isinstance(exc, users.UserExists):
        return ApiError(409, "user_exists", str(exc))
    if isinstance(exc, users.LastAdminError):
        return ApiError(409, "last_admin", str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _existing(conn: sqlite3.Connection, username: str) -> str:
    """The normalized username, or 404."""
    try:
        name = users.normalize_username(username)
    except users.UserError:
        name = None
    if name is None or users.get(conn, name) is None:
        raise HTTPException(status_code=404, detail=f"No user '{username}'.")
    return name


@router.get("")
def list_users() -> list[UserOut]:
    with db.connect() as conn:
        return [UserOut(**row) for row in users.list_all(conn)]


@router.post("", status_code=201)
def create_user(body: UserCreate, actor: CurrentUser) -> UserOut:
    with db.connect() as conn:
        try:
            user = users.create(conn, body.username, body.password, body.role)
        except users.UserError as exc:
            raise _error(exc) from exc
        audit.record(actor.username, "user.create", user.username, f"role {user.role}", conn)
        return UserOut(**users.info(conn, user.username))


@router.get("/{username}")
def get_user(username: str) -> UserOut:
    with db.connect() as conn:
        return UserOut(**users.info(conn, _existing(conn, username)))


@router.patch("/{username}")
def update_user(username: str, body: UserUpdate, actor: CurrentUser) -> UserOut:
    """Change a role and/or reset a password. Both apply, or neither does."""
    with db.connect() as conn:
        name = _existing(conn, username)
        try:
            if body.password is not None:
                users.set_password(conn, name, body.password)
            if body.role is not None:
                users.set_role(conn, name, body.role, keep_admin=True)
        except users.UserError as exc:
            raise _error(exc) from exc
        changes = [f"role {body.role}"] if body.role is not None else []
        changes += ["password reset"] if body.password is not None else []
        audit.record(actor.username, "user.update", name, ", ".join(changes), conn)
        return UserOut(**users.info(conn, name))


@router.delete("/{username}", status_code=204)
def delete_user(username: str, actor: CurrentUser) -> Response:
    """Remove a user and sign them out. The last admin can't be removed."""
    with db.connect() as conn:
        name = _existing(conn, username)
        try:
            users.delete(conn, name, keep_admin=True)
        except users.UserError as exc:
            raise _error(exc) from exc
        audit.record(actor.username, "user.delete", name, conn=conn)
    return Response(status_code=204)
