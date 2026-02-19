"""Tests for API-layer request protection middleware."""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from tako_vm.config import TakoVMConfig
from tako_vm.server.limits import ApiProtectionMiddleware


def create_client(
    *,
    api_max_payload_bytes: int = 1024,
    api_rate_limit_enabled: bool = True,
    api_rate_limit_requests: int = 2,
    api_rate_limit_window_seconds: int = 60,
) -> TestClient:
    """Create a minimal app client with API protection middleware."""
    config = TakoVMConfig(
        api_max_payload_bytes=api_max_payload_bytes,
        api_rate_limit_enabled=api_rate_limit_enabled,
        api_rate_limit_requests=api_rate_limit_requests,
        api_rate_limit_window_seconds=api_rate_limit_window_seconds,
    )

    app = FastAPI()
    app.add_middleware(ApiProtectionMiddleware, config_getter=lambda: config)

    @app.get("/limited")
    async def limited_endpoint():
        return {"ok": True}

    @app.post("/echo")
    async def echo_endpoint(request: Request):
        body = await request.body()
        return {"size": len(body)}

    return TestClient(app)


class TestApiRateLimiting:
    """Rate limiting behavior."""

    def test_rate_limit_enforced(self):
        """Requests over the configured threshold return 429."""
        with create_client(api_rate_limit_requests=2, api_rate_limit_window_seconds=60) as client:
            assert client.get("/limited").status_code == 200
            assert client.get("/limited").status_code == 200

            blocked = client.get("/limited")
            assert blocked.status_code == 429
            assert "Retry-After" in blocked.headers
            assert int(blocked.headers["Retry-After"]) >= 1

            payload = blocked.json()
            assert "rate limit" in payload["detail"].lower()
            assert isinstance(payload.get("correlation_id"), str)
            assert payload["correlation_id"]

    def test_docs_endpoints_are_exempt(self):
        """Docs and schema endpoints bypass rate limiting."""
        with create_client(api_rate_limit_requests=1, api_rate_limit_window_seconds=60) as client:
            for _ in range(3):
                assert client.get("/docs").status_code == 200
                assert client.get("/openapi.json").status_code == 200

            # Exempt endpoints do not consume the normal quota.
            assert client.get("/limited").status_code == 200
            assert client.get("/limited").status_code == 429

    def test_rate_limit_can_be_disabled(self):
        """Disabling rate limiting allows repeated requests."""
        with create_client(api_rate_limit_enabled=False, api_rate_limit_requests=1) as client:
            for _ in range(5):
                assert client.get("/limited").status_code == 200


class TestApiPayloadLimit:
    """Payload size enforcement behavior."""

    def test_oversized_payload_rejected(self):
        """Payloads above configured max bytes return 413."""
        with create_client(api_max_payload_bytes=1024) as client:
            response = client.post(
                "/echo",
                content=b"x" * 1025,
                headers={"Content-Type": "application/octet-stream"},
            )

            assert response.status_code == 413
            payload = response.json()
            assert "payload too large" in payload["detail"].lower()
            assert "1024" in payload["detail"]
            assert isinstance(payload.get("correlation_id"), str)
            assert payload["correlation_id"]

    def test_payload_at_limit_allowed(self):
        """Payloads at configured max bytes are accepted."""
        with create_client(api_max_payload_bytes=1024) as client:
            response = client.post(
                "/echo",
                content=b"x" * 1024,
                headers={"Content-Type": "application/octet-stream"},
            )

            assert response.status_code == 200
            assert response.json()["size"] == 1024
