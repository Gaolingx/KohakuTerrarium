"""Unit tests for ``llm/profiles/backend_registry.py`` — package-declared backend types."""

import pytest

from kohakuterrarium.errors import ConfigError
from kohakuterrarium.llm.profiles.backend_registry import (
    PackageBackendDescriptor,
    PackageBackendError,
    backend_doctor,
    load_backend_auth,
    load_backend_class,
    package_backend,
    package_backends,
)
from kohakuterrarium.llm.profiles.types import LLMBackend

from tests.unit._fakes.package_backend import (
    auth_block,
    echo_backend,
    install_backend_package,
    kt_home,  # noqa: F401
)


class TestDescriptorFromManifest:
    def test_valid_entry_keeps_every_field(self):
        d = PackageBackendDescriptor.from_manifest(
            "pkg",
            {
                "name": "echo",
                "module": "m",
                "class": "C",
                "display_name": "Echo",
                "description": "d",
                "credentials": "external",
                "provider_native_tools": ["img"],
            },
        )
        assert (d.name, d.module, d.class_name, d.package) == ("echo", "m", "C", "pkg")
        assert d.display_name == "Echo" and d.credentials == "external"
        assert d.provider_native_tools == ("img",)
        assert d.manages_own_credentials is True

    def test_class_name_alias_and_default_credentials(self):
        d = PackageBackendDescriptor.from_manifest(
            "pkg", {"name": "echo", "module": "m", "class_name": "C"}
        )
        assert d.class_name == "C"
        assert d.credentials == "api_key"
        assert d.manages_own_credentials is False

    @pytest.mark.parametrize(
        "entry, needle",
        [
            ({"module": "m", "class": "C"}, "'name'"),
            ({"name": "e", "class": "C"}, "'module'"),
            ({"name": "e", "module": "m"}, "'class'"),
            ({"name": "e", "module": "m", "class": "C", "credentials": "x"}, "x"),
            (
                {
                    "name": "e",
                    "module": "m",
                    "class": "C",
                    "provider_native_tools": "t",
                },
                "provider_native_tools",
            ),
        ],
    )
    def test_bad_entry_raises_config_error_naming_package(self, entry, needle):
        with pytest.raises(ConfigError) as exc:
            PackageBackendDescriptor.from_manifest("bad-pkg", entry)
        assert "bad-pkg" in str(exc.value) and needle in str(exc.value)

    def test_to_backend_is_a_self_named_provider_record(self):
        d = PackageBackendDescriptor.from_manifest(
            "pkg",
            {
                "name": "echo",
                "module": "m",
                "class": "C",
                "provider_native_tools": ["t"],
            },
        )
        assert d.to_backend() == LLMBackend(
            name="echo",
            backend_type="echo",
            provider_name="echo",
            provider_native_tools=["t"],
        )


class TestDescriptorAuthAndOwnership:
    def test_entry_carries_the_auth_block_and_ownership_flag(self):
        d = PackageBackendDescriptor.from_manifest(
            "kt-antigravity-login",
            {
                "name": "google-antigravity",
                "module": "m",
                "class": "C",
                "credentials": "external",
                "owns_builtin": True,
                "auth": {
                    "module": "kt_antigravity_login.auth",
                    "class": "AntigravityAuth",
                    "local_only": True,
                    "login_hint": "Run agy.",
                },
            },
        )
        assert d.owns_builtin is True
        assert d.auth is not None
        assert (d.auth.module, d.auth.class_name) == (
            "kt_antigravity_login.auth",
            "AntigravityAuth",
        )
        assert d.auth.to_public() == {"local_only": True, "login_hint": "Run agy."}

    def test_absent_block_defaults_are_unchanged(self):
        d = PackageBackendDescriptor.from_manifest(
            "pkg", {"name": "echo", "module": "m", "class": "C"}
        )
        assert d.owns_builtin is False
        assert d.auth is None

    @pytest.mark.parametrize(
        "entry, needle",
        [
            (
                {"name": "e", "module": "m", "class": "C", "owns_builtin": "yes"},
                "owns_builtin",
            ),
            ({"name": "e", "module": "m", "class": "C", "auth": "kt.auth"}, "auth"),
            ({"name": "e", "module": "m", "class": "C", "auth": {}}, "'module'"),
        ],
    )
    def test_bad_ownership_or_auth_raises_naming_package(self, entry, needle):
        with pytest.raises(ConfigError) as exc:
            PackageBackendDescriptor.from_manifest("bad-pkg", entry)
        assert "bad-pkg" in str(exc.value) and needle in str(exc.value)


class TestPackageBackends:
    def test_no_packages_means_no_backends(self, kt_home):
        assert package_backends() == {}
        assert package_backend("echo") is None

    def test_scan_does_not_import_the_module(self, kt_home):
        install_backend_package(
            kt_home, "boom", [echo_backend()], source="raise RuntimeError('imported')\n"
        )
        assert package_backend("echo").package == "boom"
        with pytest.raises(PackageBackendError) as exc:
            load_backend_class("echo")
        assert "boom" in str(exc.value) and "imported" in str(exc.value)

    def test_builtin_type_name_is_skipped(self, kt_home):
        install_backend_package(kt_home, "p", [echo_backend("openai"), echo_backend()])
        assert set(package_backends()) == {"echo"}
        assert package_backend("openai") is None

    def test_malformed_entry_skipped_others_kept(self, kt_home):
        install_backend_package(
            kt_home, "p", [{"name": "broken", "module": "m"}, echo_backend()]
        )
        assert set(package_backends()) == {"echo"}

    def test_malformed_auth_block_only_drops_its_own_entry(self, kt_home):
        install_backend_package(
            kt_home,
            "p",
            [echo_backend("bad", auth={"module": "m"}), echo_backend()],
        )
        assert set(package_backends()) == {"echo"}
        assert package_backend("bad") is None

    def test_auth_block_survives_the_scan(self, kt_home):
        install_backend_package(
            kt_home,
            "p",
            [
                echo_backend(
                    "agy",
                    credentials="external",
                    auth={"module": "m", "class": "A", "login_hint": "sign in"},
                )
            ],
        )
        assert package_backend("agy").auth.to_public() == {
            "local_only": True,
            "login_hint": "sign in",
        }

    def test_owned_builtin_claims_the_name(self, kt_home):
        install_backend_package(
            kt_home, "p", [echo_backend("openai", owns_builtin=True)]
        )
        assert package_backend("openai").package == "p"
        assert set(package_backends()) == {"openai"}

    def test_two_packages_owning_one_builtin_collide(self, kt_home):
        install_backend_package(
            kt_home, "a", [echo_backend("openai", owns_builtin=True)]
        )
        install_backend_package(
            kt_home, "b", [echo_backend("openai", owns_builtin=True)]
        )
        with pytest.raises(ConfigError) as exc:
            package_backend("openai")
        message = str(exc.value)
        assert "a" in message and "b" in message
        assert "owns_builtin" in message

    def test_invalid_entry_does_not_claim_the_name_for_others(self, kt_home):
        install_backend_package(
            kt_home, "a", [{"name": "echo", "module": "m", "class": "C", "auth": "x"}]
        )
        install_backend_package(kt_home, "b", [echo_backend()])
        assert package_backend("echo").package == "b"

    def test_collision_blocks_only_the_clashing_name(self, kt_home):
        install_backend_package(kt_home, "a", [echo_backend()])
        install_backend_package(kt_home, "b", [echo_backend(), echo_backend("other")])
        assert set(package_backends()) == {"other"}
        assert package_backend("other").package == "b"
        with pytest.raises(ConfigError) as exc:
            package_backend("echo")
        assert "a" in str(exc.value) and "b" in str(exc.value)


class TestLoadBackendClass:
    def test_returns_the_provider_class(self, kt_home):
        install_backend_package(kt_home, "p", [echo_backend()])
        cls = load_backend_class("echo")
        assert cls.__name__ == "EchoProvider"
        assert callable(cls.from_profile)

    def test_unknown_backend(self, kt_home):
        with pytest.raises(PackageBackendError, match="No installed package"):
            load_backend_class("ghost")

    def test_missing_class(self, kt_home):
        install_backend_package(
            kt_home, "p", [{"name": "x", "module": "{module}", "class": "Nope"}]
        )
        with pytest.raises(PackageBackendError, match="Class not found"):
            load_backend_class("x")

    def test_class_without_from_profile(self, kt_home):
        install_backend_package(
            kt_home, "p", [{"name": "x", "module": "{module}", "class": "NoFactory"}]
        )
        with pytest.raises(PackageBackendError, match="from_profile"):
            load_backend_class("x")


class TestLoadBackendAuth:
    def test_returns_the_auth_class(self, kt_home):
        install_backend_package(
            kt_home,
            "p",
            [
                echo_backend(
                    credentials="external",
                    auth=auth_block("ReadyAuth"),
                )
            ],
        )
        cls = load_backend_auth("echo")
        assert cls.__name__ == "ReadyAuth"
        assert cls.available() is True

    def test_no_auth_block_returns_none(self, kt_home):
        install_backend_package(kt_home, "p", [echo_backend()])
        assert load_backend_auth("echo") is None

    def test_unknown_backend(self, kt_home):
        with pytest.raises(PackageBackendError, match="No installed package"):
            load_backend_auth("ghost")

    def test_missing_auth_class(self, kt_home):
        install_backend_package(
            kt_home,
            "p",
            [echo_backend(auth=auth_block("Absent"))],
        )
        with pytest.raises(PackageBackendError, match="Class not found"):
            load_backend_auth("echo")


class TestBackendDoctor:
    def test_returns_the_status_line(self, kt_home):
        install_backend_package(kt_home, "p", [echo_backend()])
        assert backend_doctor("echo") == "echo ready"

    def test_class_without_doctor_returns_none(self, kt_home):
        source = (
            "class Plain:\n"
            "    @classmethod\n"
            "    def from_profile(cls, profile):\n"
            "        return cls()\n"
        )
        install_backend_package(
            kt_home,
            "p",
            [{"name": "x", "module": "{module}", "class": "Plain"}],
            source=source,
        )
        assert backend_doctor("x") is None

    def test_failing_check_propagates(self, kt_home):
        source = (
            "class Bad:\n"
            "    @classmethod\n"
            "    def from_profile(cls, profile):\n"
            "        return cls()\n"
            "    @classmethod\n"
            "    def doctor(cls):\n"
            "        raise RuntimeError('not logged in')\n"
        )
        install_backend_package(
            kt_home,
            "p",
            [{"name": "x", "module": "{module}", "class": "Bad"}],
            source=source,
        )
        with pytest.raises(RuntimeError, match="not logged in"):
            backend_doctor("x")
