"""The edge stack's inference runner (stacks/edge runner/runner.py) over its HTTP API.

One runner, one active backend (its MODEL_BACKEND): an Edge Impulse `.eim` model ("model"),
an ONNX model ("onnx"), or simulated results ("mock") when no model is loaded. All three
answer the same endpoints with the same result shape, so callers rarely need to care which
is active; `expected_features` reads each backend's own model description.
"""

from __future__ import annotations

import math
import time

import httpx

from p4n4_api.config import load_settings

TIMEOUT_S = 10
INFO_TTL_S = 30
# Swapped in by tests (httpx.MockTransport); None means real HTTP.
transport: httpx.AsyncBaseTransport | None = None

BACKENDS = {"model": "edge-impulse", "onnx": "onnx", "mock": "mock"}

_info_cache: tuple[float, str, dict] | None = None  # (fetched at, url, info)


class RunnerError(Exception):
    """The runner is unreachable or refused the request (`status`: its HTTP status)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def url() -> str:
    return load_settings().edge_runner_url.rstrip("/")


def _client(timeout: float = TIMEOUT_S) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=url(), timeout=timeout, transport=transport)


async def _get(path: str, timeout: float = TIMEOUT_S) -> dict:
    try:
        async with _client(timeout) as client:
            response = await client.get(path)
    except httpx.HTTPError as exc:
        raise RunnerError(f"Edge runner unreachable at {url()}: {exc}") from exc
    if response.status_code != 200:
        raise RunnerError(
            f"Edge runner: {path} returned {response.status_code}", response.status_code
        )
    return response.json()


def expected_features(info: dict) -> int | None:
    """How many values the loaded model takes, when its description says so.

    Edge Impulse reports `input_features_count`. ONNX reports the input shape, e.g.
    `[1, 4]` or `["batch", 4]`; the runner feeds a `(1, n)` array, so n is the product of
    the dimensions after the batch one, when they're all fixed numbers. Mock takes anything.
    """
    model = info.get("model") or {}
    if info.get("backend") == "model":
        count = model.get("input_features_count")
        return count if isinstance(count, int) and count > 0 else None
    if info.get("backend") == "onnx":
        dims = (model.get("input_shape") or [])[1:]
        if dims and all(isinstance(d, int) and d > 0 for d in dims):
            return math.prod(dims)
    return None


async def info(fresh: bool = False) -> dict:
    """The runner's /api/v1/info, cached for INFO_TTL_S (the model only changes on a
    runner restart)."""
    global _info_cache
    now = time.monotonic()
    if not fresh and _info_cache and _info_cache[1] == url() and now - _info_cache[0] < INFO_TTL_S:
        return _info_cache[2]
    data = await _get("/api/v1/info")
    _info_cache = (now, url(), data)
    return data


def clear_cache() -> None:
    global _info_cache
    _info_cache = None


async def health(timeout: float = TIMEOUT_S) -> dict:
    return await _get("/health", timeout)


async def infer(values: list[float], device: str) -> dict:
    try:
        async with _client() as client:
            response = await client.post("/api/v1/infer", json={"values": values, "device": device})
    except httpx.HTTPError as exc:
        raise RunnerError(f"Edge runner unreachable at {url()}: {exc}") from exc
    if response.status_code != 200:
        try:
            message = response.json().get("error") or response.text
        except ValueError:
            message = response.text
        raise RunnerError(f"Edge runner: {message}", response.status_code)
    return response.json()
