# PyInstaller spec — the reproducible version of the build command that
# was worked out interactively (see git history). Two things this spec
# gets right that a naive `pyinstaller app_main.py` wouldn't:
#
#   1. templates/ and config.default.yaml go at the root of the bundle
#      (matching paths.bundled_resource()'s lookup), but schema.sql
#      has to go under jobtracker/db/ specifically — importlib.resources
#      mirrors the package's dotted path inside the frozen bundle, not
#      wherever you happen to --add-data it to. Confirmed by testing
#      the actual built app, not by reasoning about it.
#
#   2. The icon differs per platform: .icns on macOS, .ico on Windows.
#      PyInstaller doesn't pick this automatically from one bundle spec
#      run on both OSes in CI.
#
# Build with: pyinstaller build_assets/jobtracker.spec
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # project root; SPECPATH is injected by PyInstaller
SRC = ROOT / "src" / "jobtracker"

icon = str(ROOT / "build_assets" / ("icon.icns" if sys.platform == "darwin" else "icon.ico"))

a = Analysis(
    [str(SRC / "app_main.py")],
    pathex=[str(ROOT / "src")],
    datas=[
        (str(SRC / "templates"), "templates"),
        (str(SRC / "config.default.yaml"), "."),
        (str(SRC / "db" / "schema.sql"), "jobtracker/db"),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="jobtracker",
    console=False,  # windowed — no terminal behind the GUI
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="jobtracker",
)

# macOS only: wraps the COLLECT output into an actual jobtracker.app bundle.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="jobtracker.app",
        icon=icon,
        bundle_identifier="com.jobtracker.app",
        info_plist={
            "CFBundleName": "jobtracker",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
        },
    )
