"""Tests for the standard error body."""

from __future__ import annotations

from fastapi.testclient import TestClient

from p4n4_api.main import create_app


def _error(r) -> dict:
    assert set(r.json()) == {"error"}, r.json()
    return r.json()["error"]


def test_http_errors_have_codes(anon, client, multi_project):
    assert _error(anon.get("/api/v1/project"))["code"] == "unauthorized"
    assert _error(client.get("/api/v1/users"))["code"] == "forbidden"
    err = _error(client.get("/api/v1/stacks/nope"))
    assert err == {"code": "not_found", "message": "Stack 'nope' not found in this project."}


def test_routing_errors(anon):
    assert _error(anon.get("/api/v1/nope"))["code"] == "not_found"
    assert _error(anon.delete("/health"))["code"] == "method_not_allowed"


def test_headers_kept(anon):
    r = anon.get("/api/v1/project")
    assert r.headers["WWW-Authenticate"] == "Bearer"
    for _ in range(10):
        anon.post("/api/v1/auth/token", json={"username": "x", "password": "y"})
    r = anon.post("/api/v1/auth/token", json={"username": "x", "password": "y"})
    assert _error(r)["code"] == "rate_limited"
    assert "Retry-After" in r.headers


def test_validation_error_lists_fields(anon):
    r = anon.post("/api/v1/auth/refresh", json={})
    assert r.status_code == 422
    err = _error(r)
    assert err["code"] == "validation_error"
    assert err["message"] == "Invalid request: body.refresh_token: Field required"
    assert err["fields"] == [{"loc": ["body", "refresh_token"], "message": "Field required"}]


def test_specific_codes(admin):
    r = admin.post("/api/v1/users", json={"username": "root", "password": "long enough pw"})
    assert (r.status_code, _error(r)["code"]) == (409, "user_exists")
    r = admin.delete("/api/v1/users/root")
    assert (r.status_code, _error(r)["code"]) == (409, "last_admin")
    # Plain input errors from the users module are validation errors.
    r = admin.post("/api/v1/users", json={"username": "a", "password": "long enough pw"})
    assert (r.status_code, _error(r)["code"]) == (422, "validation_error")
    assert "fields" not in _error(r)


def test_unhandled_error_hides_details(monkeypatch, flat_project):
    monkeypatch.setenv("P4N4_API_AUTH", "off")

    def boom(*args, **kwargs):
        raise RuntimeError("secret internals")

    monkeypatch.setattr("p4n4_api.routes.project.validate_project", boom)
    c = TestClient(create_app(), raise_server_exceptions=False)
    r = c.get("/api/v1/project/validate")
    assert r.status_code == 500
    assert _error(r) == {"code": "internal_error", "message": "Internal server error."}


def test_openapi_documents_error_body(anon):
    spec = anon.get("/openapi.json").json()
    responses = spec["paths"]["/api/v1/users"]["post"]["responses"]
    assert "422" not in responses  # FastAPI's own shape would be wrong now
    assert responses["4XX"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ErrorResponse"
    )
    assert "HTTPValidationError" not in spec["components"]["schemas"]
