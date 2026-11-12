"""Unit tests for ``llm/profiles/package_auth.py`` — the ``auth:`` block contract."""

import pytest

from kohakuterrarium.errors import ConfigError
from kohakuterrarium.llm.profiles.package_auth import (
    MAX_LOGIN_HINT,
    MAX_STATUS_SOURCE,
    UNKNOWN_AUTH_STATE,
    PackageAuthDescriptor,
    PackageAuthError,
    normalize_status,
)

WHERE = "llm_backends entry 'agy' in package 'kt-antigravity-login'"


class TestAuthDescriptorFromManifest:
    def test_valid_block_keeps_every_field(self):
        d = PackageAuthDescriptor.from_manifest(
            {
                "module": "kt_antigravity_login.auth",
                "class": "AntigravityAuth",
                "local_only": True,
                "login_hint": "Run agy to sign in.",
            },
            WHERE,
        )
        assert d.module == "kt_antigravity_login.auth"
        assert d.class_name == "AntigravityAuth"
        assert d.local_only is True
        assert d.login_hint == "Run agy to sign in."

    def test_class_name_alias_and_defaults(self):
        d = PackageAuthDescriptor.from_manifest(
            {"module": "m", "class_name": "A"}, WHERE
        )
        assert d.class_name == "A"
        assert d.local_only is True
        assert d.login_hint == ""

    def test_hint_is_stripped_and_public_view_hides_the_import(self):
        d = PackageAuthDescriptor.from_manifest(
            {"module": "m", "class": "A", "login_hint": "  hi  "}, WHERE
        )
        assert d.login_hint == "hi"
        assert d.to_public() == {"local_only": True, "login_hint": "hi"}

    def test_remote_status_can_be_opted_in(self):
        d = PackageAuthDescriptor.from_manifest(
            {"module": "m", "class": "A", "local_only": False}, WHERE
        )
        assert d.local_only is False

    @pytest.mark.parametrize(
        "raw, needle",
        [
            ("kt.auth", "mapping"),
            ({}, "'module'"),
            ({"module": "m"}, "'class'"),
            ({"class": "A"}, "'module'"),
            ({"module": "m", "class": "A", "local_only": "yes"}, "local_only"),
            ({"module": "m", "class": "A", "login_hint": 5}, "login_hint"),
            (
                {"module": "m", "class": "A", "login_hint": "x" * (MAX_LOGIN_HINT + 1)},
                "max",
            ),
        ],
    )
    def test_bad_block_raises_config_error_naming_package(self, raw, needle):
        with pytest.raises(ConfigError) as exc:
            PackageAuthDescriptor.from_manifest(raw, WHERE)
        message = str(exc.value)
        assert "kt-antigravity-login" in message and needle in message


class TestAuthStatusNormalization:
    def test_keeps_only_the_four_public_fields(self):
        out = normalize_status(
            {
                "state": "ready",
                "source": "credential_manager",
                "refresh_available": True,
                "hint": "ok",
                "access_token": "secret",
                "expires_at": 123,
            }
        )
        assert out == {
            "state": "ready",
            "source": "credential_manager",
            "refresh_available": True,
            "hint": "ok",
        }
        assert "secret" not in str(out)

    def test_unknown_or_non_string_state_falls_back(self):
        assert normalize_status({"state": "logged_in"})["state"] == UNKNOWN_AUTH_STATE
        assert normalize_status({"state": 7})["state"] == UNKNOWN_AUTH_STATE
        assert normalize_status({})["state"] == UNKNOWN_AUTH_STATE

    def test_defaults_and_strict_boolean(self):
        out = normalize_status({"state": "login_required"})
        assert out["source"] == ""
        assert out["refresh_available"] is False
        assert out["hint"] == ""
        assert (
            normalize_status({"refresh_available": True})["refresh_available"] is True
        )
        for truthy in (1, "false", "yes"):
            assert (
                normalize_status({"refresh_available": truthy})["refresh_available"]
                is False
            )

    def test_source_is_capped(self):
        out = normalize_status({"source": "s" * (MAX_STATUS_SOURCE + 40)})
        assert len(out["source"]) == MAX_STATUS_SOURCE

    def test_hint_is_capped(self):
        out = normalize_status({"hint": "x" * (MAX_LOGIN_HINT + 50)})
        assert len(out["hint"]) == MAX_LOGIN_HINT

    def test_every_documented_state_passes_through(self):
        for state in ("ready", "credential_store_unavailable", "refresh_busy"):
            assert normalize_status({"state": state})["state"] == state

    @pytest.mark.parametrize("raw", [None, "ready", ["ready"], 3])
    def test_non_mapping_raises(self, raw):
        with pytest.raises(PackageAuthError, match="must be a mapping"):
            normalize_status(raw)
