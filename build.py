import subprocess
import sys
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


def compile_app(spec_file: str = "app.spec") -> None:
    _preflight()
    # Invoke PyInstaller via the currently-running Python interpreter so this
    # works whether or not the venv has been activated (e.g. right-click "Run
    # Python File" from the IDE).
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", spec_file], check=True
    )


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
