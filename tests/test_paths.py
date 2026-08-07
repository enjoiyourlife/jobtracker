"""
paths tests.

writable_dir()/bundled_resource() branch on module state (FROZEN,
sys.platform) rather than taking it as an argument, since every real
call site needs the same answer with no wiring — so these tests
monkeypatch that state directly rather than through a parameter.
"""

from __future__ import annotations

import sys

from jobtracker import paths


class TestWritableDir:
    def test_dev_mode_uses_source_root(self, monkeypatch):
        monkeypatch.setattr(paths, "FROZEN", False)
        assert paths.writable_dir() == paths._SOURCE_ROOT

    def test_frozen_mode_uses_user_data_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths, "FROZEN", True)
        monkeypatch.setattr(paths, "_user_data_dir", lambda: tmp_path / "jobtracker")

        result = paths.writable_dir()

        assert result == tmp_path / "jobtracker"
        assert result.exists()  # created, not just returned


class TestUserDataDir:
    def test_macos_uses_application_support(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        result = paths._user_data_dir()
        assert "Library/Application Support/jobtracker" in str(result)

    def test_windows_uses_appdata(self, monkeypatch):
        """
        Asserts on path *parts*, not the joined string — pathlib picks
        Posix or Windows separator semantics from the OS actually
        running the test, not from sys.platform, so a literal
        backslash-joined string can't be asserted on a Mac test runner.
        """
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
        result = paths._user_data_dir()
        assert result.name == "jobtracker"
        assert "AppData" in str(result.parent) or "Roaming" in str(result.parent)

    def test_linux_uses_xdg_data_home(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert paths._user_data_dir() == tmp_path / "jobtracker"


class TestBundledResource:
    def test_dev_mode_resolves_from_package_dir_not_project_root(self, monkeypatch):
        """templates/ lives at src/jobtracker/templates/ — one level
        below the project root _SOURCE_ROOT points at, not inside it."""
        monkeypatch.setattr(paths, "FROZEN", False)
        assert paths.bundled_resource("templates", "base.html") == (
            paths._PACKAGE_DIR / "templates" / "base.html"
        )
        assert paths._PACKAGE_DIR != paths._SOURCE_ROOT

    def test_frozen_mode_resolves_from_meipass(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths, "FROZEN", True)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert paths.bundled_resource("templates", "base.html") == (
            tmp_path / "templates" / "base.html"
        )


class TestEnsureDefaultConfig:
    def test_seeds_config_when_absent(self, monkeypatch, tmp_path):
        config_path = tmp_path / "config.yaml"
        template_path = tmp_path / "config.default.yaml"
        template_path.write_text("min_score: 0\n")
        monkeypatch.setattr(paths, "CONFIG_PATH", config_path)
        monkeypatch.setattr(paths, "bundled_resource", lambda *p: template_path)

        paths.ensure_default_config()

        assert config_path.read_text() == "min_score: 0\n"

    def test_never_overwrites_existing_config(self, monkeypatch, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("min_score: 42  # user's real settings\n")
        template_path = tmp_path / "config.default.yaml"
        template_path.write_text("min_score: 0\n")
        monkeypatch.setattr(paths, "CONFIG_PATH", config_path)
        monkeypatch.setattr(paths, "bundled_resource", lambda *p: template_path)

        paths.ensure_default_config()

        assert "42" in config_path.read_text()

    def test_no_op_when_template_is_missing_too(self, monkeypatch, tmp_path):
        """Running from source, there's no bundled template — must not
        crash just because ensure_default_config() got called."""
        config_path = tmp_path / "config.yaml"
        monkeypatch.setattr(paths, "CONFIG_PATH", config_path)
        monkeypatch.setattr(paths, "bundled_resource", lambda *p: tmp_path / "nope.yaml")

        paths.ensure_default_config()

        assert not config_path.exists()
