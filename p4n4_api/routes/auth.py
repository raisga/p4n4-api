"""Auth endpoints: sign in, refresh, sign out, change password, and who am I."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from p4n4_api import auth, db, devices, users
from p4n4_api.auth import CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """A person signing in."""

    # Exactly one request shape must match, so /token knows which kind of sign-in it is.
    model_config = ConfigDict(extra="forbid")
    username: str
    password: str


class ApiKeyRequest(BaseModel):
    """A device signing in with the API key it got at registration or rotation."""

    model_config = ConfigDict(extra="forbid")
    api_key: str


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    refresh_expires_in: int
    username: str
    role: str


class DeviceTokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    device_id: str
    role: str


@router.post("/token", dependencies=[Depends(auth.rate_limit_login)])
def token(body: LoginRequest | ApiKeyRequest) -> TokenResponse | DeviceTokenResponse:
    """Sign in.

    - People send `username` and `password`, and get an access token (1 h) and a refresh
      token (7 d).
    - Devices send `api_key`, and get an access token only: they exchange the key again
      when it expires.
    """
    with db.connect() as conn:
        if isinstance(body, ApiKeyRequest):
            device = devices.authenticate(conn, body.api_key)
            if device is None:
                raise HTTPException(status_code=401, detail="Invalid API key.")
            return DeviceTokenResponse(**auth.issue_device_token(device))
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
    """Sign out: revoke the refresh token and every token from the same sign-in, including
    its access tokens. Other sign-ins (e.g. other devices) are untouched."""
    with db.connect() as conn:
        try:
            auth.revoke(conn, body.refresh_token)
        except auth.TokenError:
            pass  # Already invalid: signing out is still a success.
    return Response(status_code=204)


@router.post("/password", dependencies=[Depends(auth.rate_limit_login)])
def change_password(body: PasswordChange, user: CurrentUser) -> TokenResponse:
    """Change your own password. Every other sign-in is signed out; this one gets a new pair."""
    if user == auth.ANONYMOUS_ADMIN:
        raise HTTPException(status_code=400, detail="Auth is off: there is no account to change.")
    if user.role == users.DEVICE_ROLE:
        raise HTTPException(status_code=403, detail="Devices have API keys, not passwords.")
    with db.connect() as conn:
        # 403, not 401: the access token is fine, and clients refresh or sign out on 401.
        if users.authenticate(conn, user.username, body.current_password) is None:
            raise HTTPException(status_code=403, detail="Current password is incorrect.")
        try:
            users.set_password(conn, user.username, body.new_password)
        except users.UserError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return TokenResponse(**auth.issue_tokens(conn, users.get(conn, user.username)))


class Me(BaseModel):
    username: str
    role: str
    auth: bool  # False when P4N4_API_AUTH=off (everyone is an anonymous admin)


@router.get("/me")
def me(user: CurrentUser) -> Me:
    return Me(username=user.username, role=user.role, auth=user != auth.ANONYMOUS_ADMIN)
