"""Use Qt's built-in widget styles instead of bundling host KDE styles."""
from pathlib import Path
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
binaries = [entry for entry in binaries if Path(entry[1]).name != 'styles']
