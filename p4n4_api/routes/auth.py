"""Auth endpoints: sign in, refresh, sign out, and who am I."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from p4n4_api import auth, db, users
from p4n4_api.auth import CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    refresh_expires_in: int
    username: str
    role: str


@router.post("/token", dependencies=[Depends(auth.rate_limit_login)])
def token(body: LoginRequest) -> TokenResponse:
    """Exchange a username and password for an access token (1 h) and refresh token (7 d)."""
    with db.connect() as conn:
        user = users.authenticate(conn, body.username, body.password)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid username or password.")
        return TokenResponse(**auth.issue_tokens(conn, user))


@router.post("/refresh", dependencies=[Depends(auth.rate_limit_login)])
def refresh(body: RefreshRequest) -> TokenResponse:
    """Exchange a refresh token for a new pair. The old refresh token stops working."""
    with db.connect() as conn:
        try:
            return TokenResponse(**auth.rotate(conn, body.refresh_token))
        except auth.TokenError as exc:
            raise HTTPException(status_code=401, detail=f"Invalid refresh token: {exc}") from exc


@router.post("/logout", status_code=204)
def logout(body: RefreshRequest) -> Response:
    """Revoke the refresh token and every token from the same sign-in.

    The access token stays valid until it expires (at most 1 h); clients should drop it.
    """
    with db.connect() as conn:
        try:
            auth.revoke(conn, body.refresh_token)
        except auth.TokenError:
            pass  # Already invalid: signing out is still a success.
    return Response(status_code=204)


@router.get("/me")
def me(user: CurrentUser) -> dict:
    return {"username": user.username, "role": user.role, "auth": user != auth.ANONYMOUS_ADMIN}
