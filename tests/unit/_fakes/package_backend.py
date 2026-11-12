"""Build installed packages that declare ``llm_backends:`` for unit tests.

Packages are written under ``$KT_CONFIG_DIR/packages`` so every real reader
(walk, slots, backend registry) sees them without monkeypatching.
"""

import sys
import uuid
from pathlib import Path

import pytest
import yaml

from kohakuterrarium.llm.presets import catalog as presets_mod

PROVIDER_SOURCE = '''
from kohakuterrarium.llm.base import BaseLLMProvider, ChatResponse, LLMConfig


class EchoProvider(BaseLLMProvider):
    """Echo the last user message; records the profile it was built from."""

    built_from = []

    @classmethod
    def from_profile(cls, profile):
        cls.built_from.append(profile)
        return cls(LLMConfig(model=profile.model))

    @classmethod
    def doctor(cls):
        return "echo ready"

    async def _stream_chat(self, messages, *, tools=None, **kwargs):
        yield "echo:" + str(messages[-1].get("content"))

    async def _complete_chat(self, messages, **kwargs):
        return ChatResponse("echo", "stop", {}, self.config.model)


class NoFactory:
    """A class that does not follow the provider contract."""


class ReadyAuth:
    """Reports a signed-in user."""

    @classmethod
    def available(cls):
        return True


class MissingAuth:
    """Reports no signed-in user."""

    @classmethod
    def available(cls):
        return False


class BrokenAuth:
    """Raises instead of reporting."""

    @classmethod
    def available(cls):
        raise RuntimeError("auth probe exploded")
'''


@pytest.fixture
def kt_home(tmp_path, monkeypatch):
    """Isolated config root; restores ``sys.path``, imported modules and preset caches."""
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(presets_mod, "_all_presets_cache", None)
    monkeypatch.setattr(presets_mod, "_all_presets_key", None)
    before = set(sys.modules)
    yield tmp_path
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


def install_backend_package(
    home: Path,
    package: str,
    backends: list[dict],
    *,
    source: str = PROVIDER_SOURCE,
    presets: list[dict] | None = None,
) -> str:
    """Write a package whose module holds ``source``; returns the module name.

    Each backend entry may use ``module: "{module}"`` to point at that module.
    """
    module = f"kt_test_backend_{uuid.uuid4().hex[:8]}"
    root = home / "packages" / package
    root.mkdir(parents=True)
    (root / f"{module}.py").write_text(source, encoding="utf-8")
    entries = [_format_entry(e, module) for e in backends]
    manifest = {"name": package, "version": "0.1.0", "llm_backends": entries}
    if presets:
        manifest["llm_presets"] = presets
    (root / "kohaku.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return module


def _format_entry(value, module: str):
    """Substitute ``{module}`` in every string of an entry, nested included."""
    if isinstance(value, str):
        return value.format(module=module)
    if isinstance(value, dict):
        return {k: _format_entry(v, module) for k, v in value.items()}
    if isinstance(value, list):
        return [_format_entry(v, module) for v in value]
    return value


def echo_backend(name: str = "echo", **extra) -> dict:
    """A valid ``llm_backends:`` entry pointing at ``EchoProvider``."""
    return {"name": name, "module": "{module}", "class": "EchoProvider", **extra}


def auth_block(class_name: str = "ReadyAuth", **extra) -> dict:
    """An ``auth:`` block pointing at one of the fake auth classes."""
    return {"module": "{module}", "class": class_name, **extra}
