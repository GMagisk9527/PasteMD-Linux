"""Linux UI settings and desktop launcher integration."""
import json
import os
from pathlib import Path
import sys
import tempfile

from ..utils.apprules import DEFAULT_WORKFLOWS, WORKFLOW_ORDER

ROOT = Path(__file__).resolve().parents[2]
FLATPAK_APP_ID = 'io.github.GMagisk9527.PasteMDLinux'
DEFAULT_UA = ('User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
DEFAULTS = {'hotkey': 'Ctrl+Shift+B', 'hotkey_enabled': True, 'auto_paste': True,
            'input_format': 'auto', 'paste_delay_ms': 250, 'notifications': True,
            # 转换增强，键名与上游 RICHQAQ/PasteMD 的 config.json 对齐
            'enable_excel': True,
            'reference_docx': None,
            'keep_original_formula': False,
            'enable_latex_replacements': True,
            'fix_single_dollar_block': True,
            'markdown_hard_line_breaks': False,
            'md_disable_first_para_indent': True,
            'html_disable_first_para_indent': True,
            'horizontal_rule_style': 'default',
            'docx_auto_table_layout': False,
            # HTML 语义恢复，键名对齐上游 html_formatting
            # （删除线 <s>/<strike> 由 pandoc 原生支持，无需转换开关）
            'html_formatting': {'css_font_to_semantic': True,
                                'bold_first_row_to_header': False,
                                'preserve_prewrap_newlines': True},
            'pandoc_request_headers': [DEFAULT_UA],
            'pandoc_filters': [],
            # 可扩展工作流（应用扩展规则），结构与上游 extensible_workflows 一致
            'extensible_workflows': {key: {'enabled': value.get('enabled', True),
                                           'apps': list(value.get('apps', []))}
                                     for key, value in DEFAULT_WORKFLOWS.items()}}


def _string_list(value):
    return [item for item in value if isinstance(item, str) and item.strip()] \
        if isinstance(value, list) else None


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
        if isinstance(default, list):
            value = _string_list(value)
            if value is None:
                continue
        elif default is None:
            # reference_docx: 接受 null 或非空字符串
            if value is not None and (not isinstance(value, str) or not value.strip()):
                continue
        elif type(value) is not type(default):
            continue
        result[key] = value
    if result['input_format'] not in ('auto', 'markdown', 'html'):
        result['input_format'] = 'auto'
    if result['horizontal_rule_style'] not in ('default', 'paragraph_border'):
        result['horizontal_rule_style'] = 'default'
    result['paste_delay_ms'] = max(100, min(2000, result['paste_delay_ms']))
    saved_workflows = result.get('extensible_workflows')
    clean = {}
    for key in WORKFLOW_ORDER:
        cfg = saved_workflows.get(key) if isinstance(saved_workflows, dict) else None
        apps = cfg.get('apps') if isinstance(cfg, dict) else None
        enabled = cfg.get('enabled', True) if isinstance(cfg, dict) else True
        clean[key] = {'enabled': bool(enabled),
                      'apps': [app for app in apps if isinstance(app, dict)]
                      if isinstance(apps, list) else []}
    result['extensible_workflows'] = clean
    saved_formatting = result.get('html_formatting')
    clean_formatting = {}
    for key, default in (('css_font_to_semantic', True),
                         ('bold_first_row_to_header', False),
                         ('preserve_prewrap_newlines', True)):
        value = saved_formatting.get(key, default) \
            if isinstance(saved_formatting, dict) else default
        clean_formatting[key] = value if isinstance(value, bool) else default
    result['html_formatting'] = clean_formatting
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


def _quote(value):
    value = str(value).replace('%', '%%').replace('\\', '\\\\')
    for char in ('"', '`', '$'):
        value = value.replace(char, '\\' + char)
    return '"' + value + '"'


def launch_command():
    if os.environ.get('FLATPAK_ID'):
        return 'flatpak run ' + _quote(os.environ['FLATPAK_ID'])
    if os.environ.get('APPIMAGE'):
        return _quote(Path(os.environ['APPIMAGE']).resolve())
    return _quote(sys.executable) + ' ' + _quote(ROOT / 'scripts/pastemd-linux.py')


def desktop_entry(minimized=False):
    command = launch_command()
    icon = os.environ.get('FLATPAK_ID') or 'pastemd-linux'
    if minimized:
        command += ' --minimized'
    return ('[Desktop Entry]\nType=Application\nName=PasteMD Linux\n'
            'Comment=将 Markdown 和网页公式粘贴到 WPS\n'
            f'Exec={command}\nIcon={icon}\n'
            'Terminal=false\nCategories=Office;Utility;\nStartupNotify=false\n')


def autostart_path():
    if os.environ.get('FLATPAK_ID'):
        # Flatpak redirects XDG_CONFIG_HOME to its private app directory. KDE
        # reads the host autostart directory, exposed by our filesystem grant.
        return Path.home() / '.config' / 'autostart' / 'pastemd-linux.desktop'
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'autostart' / 'pastemd-linux.desktop'


def set_autostart(enabled):
    target = autostart_path()
    if enabled:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(desktop_entry(minimized=True))
    else:
        target.unlink(missing_ok=True)


def install_launcher():
    if os.environ.get('FLATPAK_ID'):
        return Path('/app/share/applications') / (os.environ['FLATPAK_ID'] + '.desktop')
    target = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'applications/pastemd-linux.desktop'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(desktop_entry())
    icon = target.parent.parent / 'icons/hicolor/256x256/apps/pastemd-linux.png'
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes((ROOT / 'assets/icons/logo.png').read_bytes())
    return target
