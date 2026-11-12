"""Package-declared sign-in status for ``credentials: external`` backends.

A ``llm_backends:`` entry may carry an ``auth:`` block naming a class the core
calls to report whether the user is signed in. That class is loaded through the
same package import path as the provider, so a backend can expose its sign-in
row in Settings without contributing any UI extension.
"""

from dataclasses import dataclass
from typing import Any

from kohakuterrarium.errors import ConfigError, LLMNotConfiguredError

AUTH_STATES = (
    "ready",
    "expired",
    "login_required",
    "unsupported_platform",
    "ambiguous_sources",
    "malformed_credential",
    "credential_store_unavailable",
    "refresh_busy",
    "unavailable",
)
UNKNOWN_AUTH_STATE = "unavailable"
MAX_LOGIN_HINT = 240
MAX_STATUS_SOURCE = 64


class PackageAuthError(LLMNotConfiguredError):
    """A package auth class is unimportable or returned an unusable result."""


@dataclass(frozen=True)
class PackageAuthDescriptor:
    """One ``llm_backends[].auth`` block, validated but not imported."""

    module: str
    class_name: str
    local_only: bool = True
    login_hint: str = ""

    @classmethod
    def from_manifest(cls, raw: Any, where: str) -> "PackageAuthDescriptor":
        """Validate an ``auth:`` block; raises ``ConfigError`` naming the entry."""
        if not isinstance(raw, dict):
            raise ConfigError(f"{where} auth must be a mapping")
        module = raw.get("module")
        class_name = raw.get("class") or raw.get("class_name")
        if not isinstance(module, str) or not module:
            raise ConfigError(f"{where} auth needs a 'module'")
        if not isinstance(class_name, str) or not class_name:
            raise ConfigError(f"{where} auth needs a 'class'")
        local_only = raw.get("local_only", True)
        if not isinstance(local_only, bool):
            raise ConfigError(f"{where} auth local_only must be true or false")
        hint = raw.get("login_hint") or ""
        if not isinstance(hint, str):
            raise ConfigError(f"{where} auth login_hint must be a string")
        hint = hint.strip()
        if len(hint) > MAX_LOGIN_HINT:
            raise ConfigError(
                f"{where} auth login_hint is {len(hint)} chars; max {MAX_LOGIN_HINT}"
            )
        return cls(
            module=module,
            class_name=class_name,
            local_only=local_only,
            login_hint=hint,
        )

    def to_public(self) -> dict[str, Any]:
        """The fields a client may see; ``module`` and ``class`` stay internal."""
        return {"local_only": self.local_only, "login_hint": self.login_hint}


def normalize_status(raw: Any) -> dict[str, Any]:
    """Reduce a package ``status()`` result to the four public fields.

    Unknown states collapse to ``unavailable`` and every other key is dropped,
    so a provider cannot leak credential material through the HTTP surface.

    Raises:
        PackageAuthError: ``raw`` is not a mapping.
    """
    if not isinstance(raw, dict):
        raise PackageAuthError(
            f"auth status must be a mapping, got {type(raw).__name__}"
        )
    state = raw.get("state")
    return {
        "state": state if state in AUTH_STATES else UNKNOWN_AUTH_STATE,
        "source": str(raw.get("source") or "")[:MAX_STATUS_SOURCE],
        "refresh_available": raw.get("refresh_available") is True,
        "hint": str(raw.get("hint") or "")[:MAX_LOGIN_HINT],
    }
