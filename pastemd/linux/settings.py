"""Linux UI settings and desktop launcher integration."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid

from ..utils.apprules import DEFAULT_WORKFLOWS, WORKFLOW_ORDER

ROOT = Path(__file__).resolve().parents[2]
FLATPAK_APP_ID = 'io.github.GMagisk9527.PasteMDLinux'
DEFAULT_UA = ('User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
DEFAULTS = {'hotkey': 'Ctrl+Shift+B', 'hotkey_enabled': True, 'auto_paste': True,
            'input_format': 'auto', 'paste_delay_ms': 250, 'notifications': True,
            # 焦点窗口未匹配任何应用规则时的行为：
            # clipboard=留在剪贴板供手动粘贴（默认）；convert_anyway=仍自动
            # 粘贴（恒按文档流转换）；ask=每次询问
            'no_app_action': 'clipboard',
            # 代码块语法高亮配色（docx writer 的 --highlight-style；
            # default=历史行为即 tango，none=纯文本不加 token 样式）
            'code_highlight_style': 'default',
            # 转换增强，键名与上游 RICHQAQ/PasteMD 的 config.json 对齐
            'enable_excel': True,
            'reference_docx': None,
            # 保留生成的 DOCX：keep_file 开启时，原生剪贴板流程在粘贴之外
            # 额外把 DOCX 落盘到 save_dir（对齐上游 keep_file/save_dir）
            'keep_file': False,
            'save_dir': None,
            # 粘贴完成后把光标移到文档末尾（对齐上游 move_cursor_to_end；
            # Linux 用 XTest 发 Ctrl+End，落点是文档末尾而非插入内容末尾）
            'move_cursor_to_end': True,
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
            # 按转换类型配置的过滤器（对齐上游 pandoc_filters_by_conversion）：
            # 键为 md_to_docx / html_to_docx / md_to_md / html_to_md /
            # md_to_latex / html_to_latex / md_to_html / html_to_html
            'pandoc_filters_by_conversion': {},
            # 可扩展工作流（应用扩展规则），结构与上游 extensible_workflows 一致
            'extensible_workflows': {key: {'enabled': value.get('enabled', True),
                                           'apps': list(value.get('apps', []))}
                                     for key, value in DEFAULT_WORKFLOWS.items()}}


def _string_list(value):
    return [item for item in value if isinstance(item, str) and item.strip()] \
        if isinstance(value, list) else None


def config_file():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'pastemd-linux' / 'settings.json'


def _recover_corrupt_settings(source, error):
    backup = source.with_name(
        f'{source.name}.broken-{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}')
    try:
        shutil.copy2(source, backup)
    except OSError:
        backup = None
    restored = False
    if backup:
        try:
            save_settings(DEFAULTS)
            restored = True
        except OSError:
            pass
    backup_note = f'已备份到 {backup}' if backup else '备份失败'
    restore_note = '已恢复默认设置' if restored else '恢复默认设置失败'
    raise RuntimeError(f'无法读取 Linux 设置：{error}；{backup_note}；{restore_note}') from error


def load_settings():
    result = dict(DEFAULTS)
    try:
        saved = json.loads(config_file().read_text())
    except FileNotFoundError:
        return result
    except (ValueError, OSError) as error:
        _recover_corrupt_settings(config_file(), error)
    if not isinstance(saved, dict):
        _recover_corrupt_settings(
            config_file(), ValueError('设置文件必须是 JSON 对象'))
    for key, default in DEFAULTS.items():
        value = saved.get(key, default)
        if isinstance(default, list):
            value = _string_list(value)
            if value is None:
                continue
        elif default is None:
            # reference_docx / save_dir: 接受 null 或非空字符串
            if value is not None and (not isinstance(value, str) or not value.strip()):
                continue
        elif type(value) is not type(default):
            continue
        result[key] = value
    if result['input_format'] not in ('auto', 'markdown', 'html'):
        result['input_format'] = 'auto'
    if result['horizontal_rule_style'] not in ('default', 'paragraph_border'):
        result['horizontal_rule_style'] = 'default'
    if result['no_app_action'] not in ('clipboard', 'convert_anyway', 'ask'):
        result['no_app_action'] = 'clipboard'
    if result['code_highlight_style'] not in ('default', 'none', 'tango', 'pygments',
                                              'kate', 'monochrome', 'espresso',
                                              'zenburn'):
        result['code_highlight_style'] = 'default'
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
    saved_by_conversion = result.get('pandoc_filters_by_conversion')
    clean_by_conversion = {}
    if isinstance(saved_by_conversion, dict):
        for key, value in saved_by_conversion.items():
            entries = _string_list(value)
            if entries and isinstance(key, str):
                clean_by_conversion[key] = entries
    result['pandoc_filters_by_conversion'] = clean_by_conversion
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


def _atomic_write_text(target, text):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=f'.{target.name}-')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
        os.chmod(name, 0o644)
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def set_autostart(enabled):
    target = autostart_path()
    if enabled:
        _atomic_write_text(target, desktop_entry(minimized=True))
    else:
        target.unlink(missing_ok=True)


def install_launcher():
    if os.environ.get('FLATPAK_ID'):
        return Path('/app/share/applications') / (os.environ['FLATPAK_ID'] + '.desktop')
    target = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'applications/pastemd-linux.desktop'
    _atomic_write_text(target, desktop_entry())
    icon = target.parent.parent / 'icons/hicolor/256x256/apps/pastemd-linux.png'
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes((ROOT / 'assets/icons/logo.png').read_bytes())
    return target
