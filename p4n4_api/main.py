"""p4n4-api application factory and entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from p4n4_api import __version__, auth, db, users
from p4n4_api.config import load_settings
from p4n4_api.routes import auth as auth_routes
from p4n4_api.routes import edge, health, project, stacks

log = logging.getLogger("p4n4_api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    with db.connect() as conn:  # creates and migrates the database before serving
        user_count = users.count(conn)
    auth.jwt_secret()
    users.warm_up()
    if not settings.auth_enabled:
        log.warning("P4N4_API_AUTH=off: every request is treated as an admin. Development only.")
    elif user_count == 0:
        log.warning("No users yet. Create an admin with: p4n4-api users add admin --role admin")
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="p4n4-api",
        description="REST API gateway for the P4N4 platform.",
        version=__version__,
        docs_url="/swagger-ui",
        lifespan=lifespan,
    )
    origins = load_settings().cors_origins
    if origins:
        # Bearer tokens go in the Authorization header, so no cookies: credentials stay off,
        # which also keeps a "*" origin safe.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(origins),
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )
    app.include_router(health.router)

    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(health.version_router)
    api_v1.include_router(auth_routes.router)
    operator = [Depends(auth.require_role("operator"))]
    api_v1.include_router(project.router, dependencies=operator)
    api_v1.include_router(stacks.router, dependencies=operator)
    api_v1.include_router(edge.router, dependencies=operator)
    app.include_router(api_v1)
    return app


app = create_app()
