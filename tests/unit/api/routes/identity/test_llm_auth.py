"""Package-declared provider auth routes (``/settings/backends/{name}/auth-*``).

A package's ``auth:`` class is the only thing these routes call. Every case
here pins the wire contract: admin gating, host-only access, the four public
status fields, bounded call time, and error text that never leaks internals.
"""

import httpx
import pytest
from fastapi import FastAPI

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.auth.dependencies import verify_admin_token
from kohakuterrarium.api.routes.identity import llm as llm_routes

from tests.unit._fakes.package_backend import (
    PROVIDER_SOURCE,
    auth_block,
    echo_backend,
    install_backend_package,
    kt_home,  # noqa: F401
)

AUTH_SOURCE = "import asyncio\n" + PROVIDER_SOURCE + """

class ReadyAuth:
    @classmethod
    def status(cls):
        return {"state": "ready", "source": "agy", "refresh_available": False,
                "hint": "signed in"}

    @classmethod
    async def refresh(cls):
        return {"state": "ready", "source": "refreshed"}

    @classmethod
    async def models(cls):
        return {"models": [{"name": "gemini-4-carbon"}]}


class LeakyAuth:
    @classmethod
    def status(cls):
        return {
            "state": "not-a-real-state",
            "source": "a" * 200,
            "refresh_available": "true",
            "hint": "h",
            "access_token": "secret-token",
        }


class BoomAuth:
    @classmethod
    def status(cls):
        raise RuntimeError("Traceback C:/Users/secret/creds.json")


class SlowAuth:
    @classmethod
    async def refresh(cls):
        await asyncio.sleep(5)
        return {"state": "ready"}


class BusyAuth:
    @classmethod
    async def refresh(cls):
        return {"state": "refresh_busy"}


class StatuslessAuth:
    @classmethod
    async def refresh(cls):
        return {"state": "ready"}


class StatusOnly:
    @classmethod
    def status(cls):
        return {"state": "ready"}


class BadModelsAuth:
    @classmethod
    async def models(cls):
        return {"items": []}
"""


def _install(home, auth_class: str = "ReadyAuth", **auth_extra) -> None:
    install_backend_package(
        home,
        "p",
        [
            echo_backend(
                "agy",
                credentials="external",
                auth=auth_block(auth_class, **auth_extra),
            )
        ],
        source=AUTH_SOURCE,
    )


def _client(*, admin: bool) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(llm_routes.router, prefix="/settings")
    if admin:
        app.dependency_overrides[verify_admin_token] = lambda: None
    app.state.auth_config = AuthConfig(admin_token="test-admin")
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


class TestAuthStatus:
    @pytest.mark.asyncio
    async def test_status_requires_admin(self, kt_home):
        _install(kt_home)
        async with _client(admin=False) as client:
            denied = await client.get("/settings/backends/agy/auth-status")
            assert denied.status_code == 401
            assert denied.headers["x-auth-required"] == "admin"
            allowed = await client.get(
                "/settings/backends/agy/auth-status",
                headers={"x-admin-token": "test-admin"},
            )
            assert allowed.status_code == 200
            assert allowed.json()["state"] == "ready"

    @pytest.mark.asyncio
    async def test_status_rejects_non_host_for_local_only(self, kt_home):
        _install(kt_home)
        async with _client(admin=True) as client:
            result = await client.get("/settings/backends/agy/auth-status?node=worker")
            assert result.status_code == 400
            assert result.json()["detail"] == "local_host_only"

    @pytest.mark.asyncio
    async def test_status_allows_remote_when_not_local_only(self, kt_home):
        _install(kt_home, "ReadyAuth", local_only=False)
        async with _client(admin=True) as client:
            result = await client.get("/settings/backends/agy/auth-status?node=worker")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_status_normalizes_provider_garbage(self, kt_home):
        _install(kt_home, "LeakyAuth")
        async with _client(admin=True) as client:
            result = await client.get("/settings/backends/agy/auth-status")
            assert result.status_code == 200
            body = result.json()
            assert set(body) == {
                "backend",
                "state",
                "source",
                "refresh_available",
                "hint",
            }
            assert body["state"] == "unavailable"
            assert len(body["source"]) == 64
            assert body["refresh_available"] is False
            assert "secret-token" not in result.text

    @pytest.mark.asyncio
    async def test_refresh_available_follows_capability(self, kt_home):
        _install(kt_home)  # ReadyAuth declares refresh
        async with _client(admin=True) as client:
            body = (await client.get("/settings/backends/agy/auth-status")).json()
            assert body["refresh_available"] is True

    @pytest.mark.asyncio
    async def test_unknown_backend_404(self, kt_home):
        async with _client(admin=True) as client:
            result = await client.get("/settings/backends/ghost/auth-status")
            assert result.status_code == 404

    @pytest.mark.asyncio
    async def test_backend_without_auth_block_404(self, kt_home):
        install_backend_package(kt_home, "p", [echo_backend("plain")])
        async with _client(admin=True) as client:
            result = await client.get("/settings/backends/plain/auth-status")
            assert result.status_code == 404

    @pytest.mark.asyncio
    async def test_status_without_the_hook_is_409(self, kt_home):
        _install(kt_home, "StatuslessAuth")
        async with _client(admin=True) as client:
            result = await client.get("/settings/backends/agy/auth-status")
            assert result.status_code == 409
            assert result.json()["detail"] == "auth_unsupported"

    @pytest.mark.asyncio
    async def test_provider_exception_is_502_without_traceback(self, kt_home):
        _install(kt_home, "BoomAuth")
        async with _client(admin=True) as client:
            result = await client.get("/settings/backends/agy/auth-status")
            assert result.status_code == 502
            assert "Traceback" not in result.text
            assert "creds.json" not in result.text


class TestAuthRefresh:
    @pytest.mark.asyncio
    async def test_refresh_returns_the_new_state(self, kt_home):
        _install(kt_home)
        async with _client(admin=True) as client:
            result = await client.post("/settings/backends/agy/auth-refresh")
            assert result.status_code == 200
            assert result.json()["source"] == "refreshed"

    @pytest.mark.asyncio
    async def test_refresh_unsupported_is_409(self, kt_home):
        _install(kt_home, "StatusOnly")
        async with _client(admin=True) as client:
            result = await client.post("/settings/backends/agy/auth-refresh")
            assert result.status_code == 409

    @pytest.mark.asyncio
    async def test_refresh_busy_is_409(self, kt_home):
        _install(kt_home, "BusyAuth")
        async with _client(admin=True) as client:
            result = await client.post("/settings/backends/agy/auth-refresh")
            assert result.status_code == 409
            assert result.json()["detail"] == "refresh_busy"

    @pytest.mark.asyncio
    async def test_refresh_timeout_maps_to_504(self, kt_home, monkeypatch):
        _install(kt_home, "SlowAuth")
        monkeypatch.setattr(llm_routes, "AUTH_CALL_TIMEOUT_S", 0.05)
        async with _client(admin=True) as client:
            result = await client.post("/settings/backends/agy/auth-refresh")
            assert result.status_code == 504, result.text


class TestAuthModels:
    @pytest.mark.asyncio
    async def test_models_returns_the_provider_list(self, kt_home):
        _install(kt_home)
        async with _client(admin=True) as client:
            result = await client.post("/settings/backends/agy/auth-models")
            assert result.status_code == 200
            body = result.json()
            assert body["backend"] == "agy"
            assert body["models"] == [{"name": "gemini-4-carbon"}]

    @pytest.mark.asyncio
    async def test_models_unsupported_is_409(self, kt_home):
        _install(kt_home, "StatusOnly")
        async with _client(admin=True) as client:
            result = await client.post("/settings/backends/agy/auth-models")
            assert result.status_code == 409

    @pytest.mark.asyncio
    async def test_unusable_models_payload_is_502(self, kt_home):
        _install(kt_home, "BadModelsAuth")
        async with _client(admin=True) as client:
            result = await client.post("/settings/backends/agy/auth-models")
            assert result.status_code == 502
