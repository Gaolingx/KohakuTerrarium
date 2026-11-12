"""Manage LLM backends, profiles, model defaults, and native-tool metadata."""

import asyncio
import inspect
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from kohakuterrarium.api.auth import verify_admin_token
from kohakuterrarium.api.routes.identity.node_routing import is_host_target
from kohakuterrarium.errors import ConfigError
from kohakuterrarium.llm.profiles.backend_registry import (
    PackageBackendDescriptor,
    PackageBackendError,
    load_backend_auth,
    package_backend,
)
from kohakuterrarium.llm.profiles.package_auth import (
    PackageAuthError,
    normalize_status,
)
from kohakuterrarium.studio.identity.llm.backends import (
    list_backend_types,
    list_backends,
    remove_backend,
    save_backend_record,
)
from kohakuterrarium.studio.identity.llm.default import (
    get_default,
    list_all_models_combined,
    resolve_and_set_default,
    set_default,
)
from kohakuterrarium.studio.identity.llm.native_tools import list_native_tools
from kohakuterrarium.studio.identity.llm.profiles import (
    list_profiles_payload,
    remove_profile,
    save_profile_record,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()

AUTH_CALL_TIMEOUT_S = 30


class BackendRequest(BaseModel):
    """Describe a named LLM backend and its provider capabilities."""

    name: str
    backend_type: str = "openai"
    base_url: str = ""
    api_key_env: str = ""
    provider_name: str = ""
    provider_native_tools: list[str] = Field(default_factory=list)


class ProfileRequest(BaseModel):
    """Describe a model profile and its generation defaults."""

    name: str
    model: str
    provider: str = ""
    max_context: int = 128000
    max_output: int = 16384
    temperature: float | None = None
    reasoning_effort: str = ""
    service_tier: str = ""
    extra_body: dict | None = None
    variation_groups: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)


class DefaultModelRequest(BaseModel):
    """Select the profile used as the default model.

    ``name`` accepts ``provider/name`` or a bare preset name that resolves
    to exactly one preset; an empty string clears the default.
    """

    name: str


@router.get("/backends")
async def get_backends():
    """Return all configured LLM backends."""
    return {"backends": list_backends()}


@router.get("/backend-types")
async def get_backend_types():
    """Return every backend type a backend record may use."""
    return {"backend_types": list_backend_types()}


@router.post("/backends", dependencies=[Depends(verify_admin_token)])
async def create_backend(req: BackendRequest):
    """Validate and persist an LLM backend record."""
    try:
        save_backend_record(
            name=req.name,
            backend_type=req.backend_type,
            base_url=req.base_url,
            api_key_env=req.api_key_env,
            provider_name=req.provider_name,
            provider_native_tools=list(req.provider_native_tools or []),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"status": "saved", "name": req.name}


def _package_auth(name: str, node: str) -> tuple[PackageBackendDescriptor, type]:
    """Resolve a backend's declared auth class, enforcing access rules."""
    try:
        descriptor = package_backend(name)
    except ConfigError as exc:
        raise HTTPException(409, str(exc)) from None
    if descriptor is None or descriptor.auth is None:
        raise HTTPException(404, f"No package-declared auth for backend {name!r}")
    if descriptor.auth.local_only and not is_host_target(node):
        raise HTTPException(400, "local_host_only")
    try:
        auth_cls = load_backend_auth(name)
    except PackageBackendError as exc:
        logger.warning("Package auth class unavailable", backend=name, error=str(exc))
        raise HTTPException(
            502, f"Auth class for backend {name!r} is unavailable"
        ) from None
    if auth_cls is None:
        raise HTTPException(404, f"No package-declared auth for backend {name!r}")
    return descriptor, auth_cls


def _status_payload(name: str, raw: Any, auth_cls: type) -> dict[str, Any]:
    """Normalize a provider's status into the public wire shape."""
    try:
        normalized = normalize_status(raw)
    except PackageAuthError as exc:
        logger.warning("Package auth status malformed", backend=name, error=str(exc))
        raise HTTPException(
            502, f"Auth status for backend {name!r} is malformed"
        ) from None
    normalized["refresh_available"] = callable(getattr(auth_cls, "refresh", None))
    return {"backend": name, **normalized}


async def _invoke_auth(name: str, action: str, method) -> Any:
    """Call a package auth hook, mapping failure to a safe HTTP error."""
    try:
        result = method()
        if inspect.isawaitable(result):
            return await asyncio.wait_for(result, timeout=AUTH_CALL_TIMEOUT_S)
        return result
    except asyncio.TimeoutError:
        raise HTTPException(504, f"Backend {name!r} auth {action} timed out") from None
    except Exception as exc:
        logger.warning(
            "Package auth call failed", backend=name, action=action, error=str(exc)
        )
        raise HTTPException(502, f"Backend {name!r} auth {action} failed") from None


@router.get("/backends/{name}/auth-status", dependencies=[Depends(verify_admin_token)])
async def get_backend_auth_status(name: str, node: str = ""):
    """Report the sign-in state a backend's package declares."""
    _descriptor, auth_cls = _package_auth(name, node)
    check = getattr(auth_cls, "status", None)
    if not callable(check):
        raise HTTPException(409, "auth_unsupported")
    try:
        raw = await asyncio.to_thread(check)
    except Exception as exc:
        logger.warning("Package auth status failed", backend=name, error=str(exc))
        raise HTTPException(502, f"Auth status for backend {name!r} failed") from None
    return _status_payload(name, raw, auth_cls)


@router.post(
    "/backends/{name}/auth-refresh", dependencies=[Depends(verify_admin_token)]
)
async def refresh_backend_auth(name: str, node: str = ""):
    """Ask a backend's package to renew credentials, then report the new state."""
    _descriptor, auth_cls = _package_auth(name, node)
    refresh = getattr(auth_cls, "refresh", None)
    if not callable(refresh):
        raise HTTPException(409, "auth_unsupported")
    payload = _status_payload(
        name, await _invoke_auth(name, "refresh", refresh), auth_cls
    )
    if payload["state"] == "refresh_busy":
        raise HTTPException(409, "refresh_busy")
    return payload


@router.post("/backends/{name}/auth-models", dependencies=[Depends(verify_admin_token)])
async def get_backend_auth_models(name: str, node: str = ""):
    """Return the model list a backend's package obtains from its own account."""
    _descriptor, auth_cls = _package_auth(name, node)
    models = getattr(auth_cls, "models", None)
    if not callable(models):
        raise HTTPException(409, "auth_unsupported")
    raw = await _invoke_auth(name, "models", models)
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), list):
        logger.warning("Package auth models unusable", backend=name)
        raise HTTPException(
            502, f"Backend {name!r} auth models returned an unusable payload"
        )
    return {"backend": name, "models": raw["models"]}


@router.delete("/backends/{name}", dependencies=[Depends(verify_admin_token)])
async def delete_backend_route(name: str):
    """Delete an LLM backend unless configuration invariants reject it."""
    try:
        deleted = remove_backend(name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not deleted:
        raise HTTPException(404, f"Provider not found: {name}")
    return {"status": "deleted", "name": name}


@router.get("/native-tools")
async def get_native_tools():
    """Return metadata for every provider-native built-in tool."""
    return {"tools": list_native_tools()}


@router.get("/profiles")
async def get_profiles():
    """Return all configured model profiles in API form."""
    return {"profiles": list_profiles_payload()}


@router.post("/profiles", dependencies=[Depends(verify_admin_token)])
async def create_profile(req: ProfileRequest):
    """Validate and persist a model profile for an existing provider."""
    try:
        save_profile_record(
            name=req.name,
            model=req.model,
            provider=req.provider,
            max_context=req.max_context,
            max_output=req.max_output,
            temperature=req.temperature,
            reasoning_effort=req.reasoning_effort,
            service_tier=req.service_tier,
            extra_body=req.extra_body or {},
            variation_groups=req.variation_groups or {},
        )
    except ValueError as e:
        msg = str(e)
        if msg.startswith("Provider not found"):
            raise HTTPException(404, msg) from e
        raise HTTPException(400, msg) from e
    return {"status": "saved", "name": req.name, "provider": req.provider}


@router.delete(
    "/profiles/{provider}/{name}", dependencies=[Depends(verify_admin_token)]
)
async def delete_profile_route(provider: str, name: str):
    """Delete a provider-qualified model profile."""
    if not remove_profile(name, provider):
        raise HTTPException(404, f"Profile not found: {provider}/{name}")
    return {"status": "deleted", "name": name, "provider": provider}


@router.get("/default-model")
async def get_default_model_route():
    """Return the configured default model profile name."""
    return {"default_model": get_default()}


@router.post("/default-model", dependencies=[Depends(verify_admin_token)])
async def set_default_model_route(req: DefaultModelRequest):
    """Persist the default model, resolving the request to ``provider/name``.

    An empty name clears the default; anything else must resolve to exactly
    one preset, so a bare name shared by several providers is rejected
    instead of silently binding to the wrong one.
    """
    if not req.name:
        set_default("")
        return {"status": "set", "default_model": ""}
    identifier, error = resolve_and_set_default(req.name)
    if error:
        if error.startswith("Preset not found"):
            raise HTTPException(404, error)
        raise HTTPException(400, error)
    return {"status": "set", "default_model": identifier}


@router.get("/models")
async def get_all_models_route():
    """Return the combined model catalog across configured backends."""
    return list_all_models_combined()
