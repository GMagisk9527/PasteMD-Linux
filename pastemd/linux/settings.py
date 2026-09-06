"""Linux UI settings and desktop launcher integration."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = {'hotkey': 'Ctrl+Shift+B', 'hotkey_enabled': True, 'auto_paste': True,
            'input_format': 'auto', 'paste_delay_ms': 250, 'notifications': True}


def config_file():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'pastemd-linux' / 'settings.json'


def load_settings():
    result = dict(DEFAULTS)
    try:
        saved = json.loads(config_file().read_text())
    except FileNotFoundError:
        return result
    except (ValueError, OSError) as error:
        raise RuntimeError('无法读取 Linux 设置：' + str(error)) from error
    if not isinstance(saved, dict):
        raise RuntimeError('Linux 设置文件必须是 JSON 对象。')
    for key, default in DEFAULTS.items():
        value = saved.get(key, default)
        if type(value) is type(default):
            result[key] = value
    if result['input_format'] not in ('auto', 'markdown', 'html'):
        result['input_format'] = 'auto'
    result['paste_delay_ms'] = max(100, min(2000, result['paste_delay_ms']))
    return result


def save_settings(values):
    target = config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=target.parent, prefix='.settings-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({key: values[key] for key in DEFAULTS}, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def desktop_entry(minimized=False):
    def quote(value):
        value = str(value).replace('%', '%%').replace('\\', '\\\\\\\\')
        for char in ('"', '`', '$'):
            value = value.replace(char, '\\' + char)
        return '"' + value + '"'
    command = quote(sys.executable) + ' ' + quote(ROOT / 'scripts/pastemd-linux.py')
    if minimized:
        command += ' --minimized'
    return ('[Desktop Entry]\nType=Application\nName=PasteMD Linux\n'
            'Comment=将 Markdown 和网页公式粘贴到 WPS\n'
            f'Exec={command}\nIcon={ROOT / "assets/icons/logo.png"}\n'
            'Terminal=false\nCategories=Office;Utility;\nStartupNotify=false\n')


def autostart_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'autostart' / 'pastemd-linux.desktop'


def set_autostart(enabled):
    target = autostart_path()
    if enabled:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(desktop_entry(minimized=True))
    else:
        target.unlink(missing_ok=True)


def install_launcher():
    target = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'applications/pastemd-linux.desktop'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(desktop_entry())
    return target
