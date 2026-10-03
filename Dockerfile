# p4n4-api image: the API on Python 3.12 (slim), non-root, with the Docker CLI and
# Compose plugin for stack status and control. Docker access is off unless the
# socket is mounted (docker-compose.docker.yml). See README "Running in Docker".
#
#   docker build -t p4n4-api .
#   docker build --build-arg P4N4_LIB="p4n4-lib @ git+https://github.com/raisga/p4n4-lib.git@v0.2.0" -t p4n4-api .

# Base images are pinned by digest (multi-arch indexes); Dependabot bumps them.
# Static docker and docker-compose binaries, copied into the runtime image.
FROM docker:29-cli@sha256:b1805116a6a86cc591b5d5f60a910a0715cdcc9d18d866ad68b1457ead25c35c AS docker-cli

FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS build
# git only for the p4n4-lib install, which isn't on PyPI yet
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ARG P4N4_LIB="p4n4-lib @ git+https://github.com/raisga/p4n4-lib.git"
RUN pip install "$P4N4_LIB"
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY p4n4_api/ p4n4_api/
RUN pip install .

FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ARG VERSION=dev
LABEL org.opencontainers.image.title="p4n4-api" \
      org.opencontainers.image.description="REST API gateway for the p4n4 platform" \
      org.opencontainers.image.source="https://github.com/raisga/p4n4-api" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="$VERSION"
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
COPY --from=build /opt/venv /opt/venv
# A fixed UID so a bind-mounted data dir can be chowned to it; /data is the named volume.
RUN useradd --system --uid 10001 --user-group --home-dir /data --no-create-home p4n4 \
 && install -d -o p4n4 -g p4n4 -m 700 /data
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONFIG=/tmp/.docker \
    P4N4_API_HOST=0.0.0.0 \
    P4N4_API_DATA_DIR=/data
USER 10001:10001
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('P4N4_API_PORT') or 8000}/health\", timeout=4)"]
ENTRYPOINT ["p4n4-api"]
CMD ["serve"]
