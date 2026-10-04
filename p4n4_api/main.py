"""p4n4-api application factory and entrypoint."""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from p4n4_api import __version__, auth, db, errors, jobs, logs, users
from p4n4_api.config import load_settings
from p4n4_api.mqtt import bridge
from p4n4_api.routes import (
    agents,
    control,
    devices,
    edge,
    health,
    inference,
    project,
    stacks,
    telemetry,
)
from p4n4_api.routes import audit as audit_routes
from p4n4_api.routes import auth as auth_routes
from p4n4_api.routes import jobs as jobs_routes
from p4n4_api.routes import (
    mqtt as mqtt_routes,
)
from p4n4_api.routes import users as users_routes

log = logging.getLogger("p4n4_api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    with db.connect() as conn:  # creates and migrates the database before serving
        created = users.seed_dev_users(conn) if settings.dev_users else []
        user_count = users.count(conn)
    auth.jwt_secret()
    users.warm_up()
    if settings.dev_users:
        log.warning(
            "P4N4_API_DEV_USERS=true: %s (password %r) can sign in. Development only.",
            ", ".join(f"{name} ({role})" for name, role in users.DEV_USERS),
            users.DEV_PASSWORD,
        )
        if created:
            log.info("Created dev users: %s", ", ".join(created))
    if not settings.auth_enabled:
        log.warning("P4N4_API_AUTH=off: every request is treated as an admin. Development only.")
    elif user_count == 0:
        log.warning(
            "No users yet. Create an admin with: p4n4-api users add admin --role admin "
            "(or `p4n4-api users bootstrap` for a generated password)"
        )
    if settings.mqtt_enabled:
        bridge.start(
            settings.mqtt_host, settings.mqtt_port, settings.mqtt_username, settings.mqtt_password
        )
    yield
    await bridge.stop()
    jobs.shutdown()


def _trusted_proxies(entries: tuple[str, ...]) -> list[str]:
    """Check P4N4_API_TRUSTED_PROXIES. uvicorn would quietly keep a typo as a literal that
    never matches, leaving every client behind the proxy sharing one rate limit."""
    for entry in entries:
        try:
            ipaddress.ip_network(entry, strict=False)
        except ValueError:
            raise RuntimeError(
                f"P4N4_API_TRUSTED_PROXIES: {entry!r} is not an IP address or network "
                "(e.g. 172.17.0.1 or 172.16.0.0/12)."
            ) from None
    return list(entries)


def create_app() -> FastAPI:
    app = FastAPI(
        title="p4n4-api",
        description="REST API gateway for the P4N4 platform.",
        version=__version__,
        docs_url="/swagger-ui",
        lifespan=lifespan,
        responses=errors.RESPONSES,
    )
    errors.install(app)
    settings = load_settings()
    # Added first, so it runs inside the proxy middleware and logs the real client address.
    app.add_middleware(logs.RequestIdMiddleware)
    if settings.trusted_proxies:
        # Behind the dashboard's nginx every request comes from the proxy's address. Take the
        # client from X-Forwarded-For (rightmost untrusted hop), so the sign-in rate limit
        # applies per client rather than to everyone at once. Only from these proxies:
        # anyone else could put any address there.
        app.add_middleware(
            ProxyHeadersMiddleware, trusted_hosts=_trusted_proxies(settings.trusted_proxies)
        )
    origins = settings.cors_origins
    if origins:
        # Bearer tokens go in the Authorization header, so no cookies: credentials stay off,
        # which also keeps a "*" origin safe.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(origins),
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", logs.HEADER],
            expose_headers=[logs.HEADER],
        )
    app.include_router(health.router)

    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(health.version_router)
    api_v1.include_router(auth_routes.router)
    # Normies read status and chat; routes that act check for operator themselves.
    normie = [Depends(auth.require_role("normie"))]
    operator = [Depends(auth.require_role("operator"))]
    api_v1.include_router(project.router, dependencies=normie)
    api_v1.include_router(stacks.router, dependencies=normie)
    # Control and logs are admin-only per route; job progress is readable by everyone.
    api_v1.include_router(control.router, dependencies=normie)
    api_v1.include_router(jobs_routes.router, dependencies=normie)
    api_v1.include_router(edge.router, dependencies=normie)
    api_v1.include_router(inference.router, dependencies=normie)
    api_v1.include_router(agents.router, dependencies=normie)
    api_v1.include_router(mqtt_routes.router, dependencies=operator)
    # Operators read the registry; its write routes add the admin check themselves.
    api_v1.include_router(devices.router, dependencies=operator)
    # Ingest is for devices, reads for normies and up: each route checks its own role.
    api_v1.include_router(telemetry.router)
    admin = [Depends(auth.require_role("admin"))]
    api_v1.include_router(users_routes.router, dependencies=admin)
    api_v1.include_router(audit_routes.router, dependencies=admin)
    app.include_router(api_v1)
    return app


app = create_app()
