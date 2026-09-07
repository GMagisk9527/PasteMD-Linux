"""Collect desktop Qt plugins without optional image codecs or theme stacks."""
from pathlib import Path
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)


def needed_plugin(entry):
    source, destination = entry
    category = Path(destination).name
    name = Path(source).name
    if category == 'imageformats':
        return name in {'libqgif.so', 'libqico.so', 'libqjpeg.so', 'libqsvg.so', 'libqwebp.so'}
    if category == 'platformthemes':
        # Qt's default theme needs no GTK/KDE libraries. Keep portal integration.
        return name == 'libqxdgdesktopportal.so'
    if category == 'platforminputcontexts':
        return name != 'libqtvirtualkeyboardplugin.so'
    return True


# Filter before PyInstaller scans binary dependencies; deleting plugins from the
# finished AppDir would leave their large transitive libraries behind.
binaries = [entry for entry in binaries if needed_plugin(entry)]
