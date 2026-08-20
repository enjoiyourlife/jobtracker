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

    def test_frozen_mode_uses_bundle_data_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths, "FROZEN", True)
        monkeypatch.setattr(paths, "_bundle_data_dir", lambda: tmp_path / "appdata")

        result = paths.writable_dir()

        assert result == tmp_path / "appdata"
        assert result.exists()  # created, not just returned


class TestBundleDataDir:
    """
    Regression coverage for a deliberate design choice, not the OS
    default: packaged-app data lives inside the .app bundle itself
    (Contents/Resources/appdata) so deleting the app removes every
    trace in one action, rather than in ~/Library/Application Support
    where it would silently survive an uninstall.
    """

    def test_resolves_relative_to_the_running_executable(self, monkeypatch, tmp_path):
        bundle = tmp_path / "jobtracker.app" / "Contents"
        executable = bundle / "MacOS" / "jobtracker"
        executable.parent.mkdir(parents=True)
        executable.touch()
        monkeypatch.setattr(sys, "executable", str(executable))

        assert paths._bundle_data_dir() == bundle / "Resources" / "appdata"


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
