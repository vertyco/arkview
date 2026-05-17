"""PyInstaller hook for the arkparser package.

Two jobs:

1. ``collect_submodules`` so every nested arkparser module is bundled (export,
   files, game_objects, models, common.map_config, etc.).
2. ``copy_metadata`` so the package's ``dist-info`` directory ships with the
   exe. Without this, ``importlib.metadata.version('arkparser')`` returns
   ``PackageNotFoundError`` at runtime and the startup banner shows
   ``arkparser : unknown``.
"""

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

hiddenimports = collect_submodules("arkparser")
datas = copy_metadata("arkparser")
