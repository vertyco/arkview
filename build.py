import os
import subprocess
import sys
import time
from pathlib import Path

from app.constants import VERSION

# Set your GitHub access token and repository name
GITHUB_ACCESS_TOKEN = "REDACTED_GITHUB_TOKEN"
GITHUB_REPO_NAME = "vertyco/arkview"

WHATS_NEW = "## What's New?\n- "
INSTALLATION = (
    "## Installation\n"
    "- Download the `ArkViewer.exe` file from the Assets section below.\n"
    "- Run the `ArkViewer.exe` file to start the application.\n"
    "- Windows may ask if you're sure you want to run the file, click `run anyway`\n"
    "- If you're running it for the first time it will create a `config.ini` file in the same directory as the executable\n"
)

# README contains non-ASCII (emoji, unicode box-drawing) - force utf-8 so
# build.py doesn't crash on Windows where the default decoder is cp1252.
_readme = Path("README.md")
MESSAGE = (
    WHATS_NEW + "\n\n" + INSTALLATION + "\n\n" + _readme.read_text(encoding="utf-8")
    if _readme.exists()
    else WHATS_NEW + "\n\n" + INSTALLATION
)


def _preflight() -> None:
    try:
        import arkparser
    except ImportError as exc:
        raise SystemExit(
            f"Preflight failed: arkparser not importable from {sys.executable}.\n"
            f"Run `pip install -r requirements.txt` first.\n"
            f"Underlying error: {exc}"
        )
    print(f"Preflight OK: arkparser {arkparser.__version__}")


def _clear_dist_exe() -> None:
    """Make `dist/ArkViewer.exe` writable before PyInstaller tries to replace it.

    Causes of the lock we've seen:
      - A previous run of the exe still alive (uac_admin=True manifest; the
        process won't die from a non-elevated taskkill).
      - Windows Defender / Explorer holding a transient handle right after a
        build.

    Strategy:
      1. Kill any running `ArkViewer.exe` process owned by the current user.
         `taskkill /F` works for same-user elevated processes when the calling
         shell is also elevated; falls through silently if it can't.
      2. If the file is still locked, rename it to `ArkViewer.exe.old`. NTFS
         allows rename without DELETE access on the source, so this usually
         succeeds even when delete does not. PyInstaller then writes the new
         file under the original name. Old files clean themselves up on the
         next successful rename.
    """
    exe = Path(__file__).parent / "dist" / "ArkViewer.exe"
    if not exe.exists():
        return

    # Best-effort kill; ignore failure (exe may not be running).
    subprocess.run(
        ["taskkill", "/F", "/IM", "ArkViewer.exe", "/T"],
        capture_output=True,
        check=False,
    )

    try:
        exe.unlink()
        return
    except PermissionError:
        pass

    # Rename out of the way so PyInstaller can write the new file.
    stamp = int(time.time())
    sidelined = exe.with_suffix(f".exe.old.{stamp}")
    try:
        os.replace(exe, sidelined)
        print(f"Note: dist/ArkViewer.exe was locked; renamed to {sidelined.name}")
    except OSError as exc:
        raise SystemExit(
            f"Cannot replace {exe}: locked and rename failed ({exc}).\n"
            "Close any running ArkViewer.exe via Task Manager, then re-run build.py."
        )


def _sweep_old_exes() -> None:
    """After a successful build, delete any sidelined `*.exe.old.*` files.

    The lock that forced the rename usually clears once PyInstaller finishes
    (Defender / Explorer release their handle). Sweeping keeps `dist/` from
    accumulating stale binaries. Failures are non-fatal — next run retries.
    """
    dist = Path(__file__).parent / "dist"
    for stale in dist.glob("ArkViewer.exe.old.*"):
        try:
            stale.unlink()
        except OSError:
            pass


def compile_app(spec_file: str = "app.spec") -> None:
    _preflight()
    _clear_dist_exe()
    # Invoke PyInstaller via the currently-running Python interpreter so this
    # works whether or not the venv has been activated (e.g. right-click "Run
    # Python File" from the IDE).
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", spec_file], check=True
    )
    _sweep_old_exes()


async def create_github_release() -> None:
    # Lazy-imported so just compiling doesn't require PyGithub to be installed.
    import github  # pip install PyGithub

    exe_path = str(Path(__file__).parent / "dist" / "ArkViewer.exe")
    print("Authenticating")
    g = github.Github(GITHUB_ACCESS_TOKEN)
    repo = g.get_repo(GITHUB_REPO_NAME)
    print("Creating new release")
    release = repo.create_git_release(
        tag=VERSION,
        name=f"Release {VERSION}",
        message=MESSAGE,
        draft=False,
        prerelease=False,
    )
    print(f"Uploading {exe_path}")
    release.upload_asset(exe_path, label="ArkViewer.exe")
    print(f"Release {VERSION} published: {release.html_url}")


if __name__ == "__main__":
    compile_app()
    # asyncio.run(create_github_release())
