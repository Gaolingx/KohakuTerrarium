"""Preset catalogue ownership: a package claiming a built-in type supplies it.

Covers the merge order in ``get_all_presets`` and the deprecation table in
``aliases.deprecated_presets`` for ``google-antigravity`` — the one built-in
type a package may take over (PLAN §7.4).
"""

import shutil

from kohakuterrarium.llm.antigravity.presets import DEPRECATED as AGY_DEPRECATED
from kohakuterrarium.llm.antigravity.presets import PRESETS as AGY_PRESETS
from kohakuterrarium.llm.presets.aliases import deprecated_presets, resolve_deprecated
from kohakuterrarium.llm.presets.catalog import get_all_presets

from tests.unit._fakes.package_backend import (
    auth_block,
    echo_backend,
    install_backend_package,
    kt_home,  # noqa: F401
)

AGY = "google-antigravity"


def _agy_entry(**extra) -> dict:
    entry = echo_backend(AGY, credentials="external", auth=auth_block("ReadyAuth"))
    entry.update(extra)
    return entry


def _preset(name: str, model: str) -> dict:
    return {"name": name, "provider": AGY, "model": model}


class TestOwnedBuiltinPresets:
    def test_package_presets_win_when_builtin_type_is_owned(self, kt_home):
        install_backend_package(
            kt_home,
            "p",
            [_agy_entry(owns_builtin=True)],
            presets=[_preset("package-only-model", "custom-model")],
        )
        assert (AGY, "package-only-model") in get_all_presets()

    def test_package_preset_with_same_name_overrides_builtin_when_owned(self, kt_home):
        builtin = next(iter(AGY_PRESETS.items()))
        (provider, name), _body = builtin
        install_backend_package(
            kt_home,
            "p",
            [_agy_entry(owns_builtin=True)],
            presets=[_preset(name, "package-model")],
        )
        assert get_all_presets()[(provider, name)]["model"] == "package-model"

    def test_package_preset_without_ownership_loses_to_the_builtin(self, kt_home):
        builtin = next(iter(AGY_PRESETS.items()))
        (provider, name), body = builtin
        install_backend_package(
            kt_home,
            "p",
            [_agy_entry()],
            presets=[_preset(name, "package-model")],
        )
        assert get_all_presets()[(provider, name)] == body

    def test_uninstalled_package_keeps_builtin_agy_presets(self, kt_home):
        install_backend_package(
            kt_home,
            "p",
            [_agy_entry(owns_builtin=True)],
            presets=[_preset("package-only-model", "custom-model")],
        )
        assert (AGY, "package-only-model") in get_all_presets()

        shutil.rmtree(kt_home / "packages" / "p")
        presets = get_all_presets()
        assert (AGY, "package-only-model") not in presets
        assert set(AGY_PRESETS).issubset(presets)

    def test_no_duplicate_keys_after_merge(self, kt_home):
        builtin = next(iter(AGY_PRESETS.items()))
        (provider, name), _body = builtin
        install_backend_package(
            kt_home,
            "p",
            [_agy_entry(owns_builtin=True)],
            presets=[_preset(name, "package-model"), _preset(name, "second")],
        )
        keys = list(get_all_presets())
        assert len(keys) == len(set(keys))
        assert get_all_presets()[(provider, name)]["model"] == "package-model"


class TestDeprecatedOwnership:
    def test_deprecated_aliases_kept_without_a_package(self, kt_home):
        mapping = deprecated_presets()
        old = next(iter(AGY_DEPRECATED))
        assert mapping[(AGY, old)] == AGY_DEPRECATED[old]
        assert resolve_deprecated(old, AGY) == [(AGY, AGY_DEPRECATED[old])]

    def test_deprecated_aliases_drop_when_owned(self, kt_home):
        install_backend_package(kt_home, "p", [_agy_entry(owns_builtin=True)])
        old = next(iter(AGY_DEPRECATED))
        assert (AGY, old) not in deprecated_presets()
        assert resolve_deprecated(old, AGY) == []
