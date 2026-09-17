#!/usr/bin/env python3
"""Experimental Fedora/Wayland entry point. Modified 2026-09-05, AGPL-3.0.

Uses desktop-managed shortcuts; does not synthesize keyboard events.
"""
import argparse
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import select
import time
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

# 直接以脚本方式运行时(--serve-clipboard 子进程、手工执行)补上仓库根目录，
# 让下面的包内绝对导入可用。
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pastemd.utils.docx_processor import DocxProcessor
from pastemd.utils.latex import convert_latex_delimiters
from pastemd.utils.spreadsheet import parse_markdown_table, table_to_html, table_to_tsv


MATH_EXTENSIONS = '+tex_math_dollars+tex_math_single_backslash+tex_math_double_backslash'


DEMO_MARKDOWN = r"""# PasteMD 公式粘贴测试

中文、**粗体**与行内公式 $x^2+y^2=z^2$。

$$\frac{a}{b}+\sqrt[3]{x}=\sum_{i=1}^{n} i$$

$$\begin{pmatrix}a & b \\ c & d\end{pmatrix}$$

| 项目 | 公式 |
|---|---|
| 判别式 | $\Delta=b^2-4ac$ |

请点击公式，检查能否编辑分子、根式和求和上下限。
"""


def clean_document(value):
    """Drop browser layout wrappers while retaining semantic text, tables and math."""
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict) and item.get('t') in ('Span', 'Div'):
                if 'katex-html' not in item['c'][0][1]:
                    result.extend(clean_document(item['c'][1]))
            elif isinstance(item, dict) and item.get('t') in ('RawInline', 'RawBlock') and item['c'][0] == 'html':
                continue
            else:
                result.append(clean_document(item))
        return result
    if isinstance(value, dict):
        return {key: clean_document(item) for key, item in value.items()}
    return value


def lua_filter(name):
    """Path of a bundled Lua filter, covering source runs and PyInstaller bundles."""
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        candidate = Path(bundle) / 'lua' / name
        if candidate.is_file():
            return str(candidate)
    return str(Path(__file__).resolve().parents[1] / 'lua' / name)


def load_conversion_options():
    """Conversion settings from settings.json; empty dict when unavailable."""
    try:
        from pastemd.linux.settings import load_settings
        return load_settings()
    except Exception:
        return {}


def _filter_args(options):
    args = []
    for path in options.get('pandoc_filters') or []:
        path = str(path)
        args += ['--lua-filter' if path.lower().endswith('.lua') else '--filter', path]
    return args


def _stage_filter_args(reader, options):
    """Lua filters mirroring the upstream PasteMD conversion chain."""
    args = []
    if options.get('enable_latex_replacements', True):
        args += ['--lua-filter', lua_filter('latex-replacements.lua')]
    if reader.startswith('markdown'):
        args += ['--lua-filter', lua_filter('normalize-markdown-breaks.lua')]
    if options.get('keep_original_formula'):
        args += ['--lua-filter', lua_filter('keep-latex-math.lua')]
    return args + _filter_args(options)


def docx_writer_args(options):
    """Args appended to `pandoc --from json` DOCX conversions."""
    options = options or {}
    args = ['--highlight-style', 'tango']
    reference = options.get('reference_docx')
    if reference:
        args += ['--reference-doc', str(reference)]
    for header in options.get('pandoc_request_headers') or []:
        args += ['--request-header', str(header)]
    return args


def finish_docx(docx, reader, options=None):
    """Upstream DOCX post-processing: indent style, rules, table layout."""
    options = options or {}
    indent = ('md_disable_first_para_indent' if reader.startswith('markdown')
              else 'html_disable_first_para_indent')
    return DocxProcessor.apply_custom_processing(
        docx,
        disable_first_para_indent=bool(options.get(indent, True)),
        horizontal_rule_style=options.get('horizontal_rule_style', 'default'),
        auto_layout_tables=bool(options.get('docx_auto_table_layout', False)),
    )


def prepare_document(content, reader, options=None):
    options = options or {}
    if reader.startswith('markdown'):
        text = content.decode('utf-8', 'replace')
        text = convert_latex_delimiters(
            text, bool(options.get('fix_single_dollar_block', True)))
        content = text.encode('utf-8')
        if options.get('markdown_hard_line_breaks'):
            reader += '+hard_line_breaks'
    document = json.loads(run(
        ['pandoc', '--from', reader, '--to', 'json'] + _stage_filter_args(reader, options),
        content))
    document = clean_document(document)
    document['meta'] = {}
    return json.dumps(document, ensure_ascii=False).encode('utf-8')


def run(command, data=None):
    result = subprocess.run(command, input=data, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip()
                           or f'{command[0]} failed ({result.returncode})')
    return result.stdout


def read_clipboard(input_format):
    types = [kind.strip() for kind in
             run(['wl-paste', '--list-types']).decode('utf-8', 'replace').splitlines()
             if kind.strip()]
    html = next((kind for kind in types
                 if kind.partition(';')[0].strip().lower() == 'text/html'), None)
    if input_format != 'markdown' and html:
        return run(['wl-paste', '--no-newline', '--type', html]), 'html' + MATH_EXTENSIONS
    if input_format == 'html':
        raise RuntimeError('剪贴板没有 text/html；请复制网页正文或使用 --input markdown。')
    plain = next((t for t in types if t.lower().startswith('text/plain')), None)
    if plain is None:
        raise RuntimeError('剪贴板没有文本。请先复制 Markdown 或网页正文。')
    return run(['wl-paste', '--no-newline', '--type', plain]), 'markdown' + MATH_EXTENSIONS


def notify(message):
    if shutil.which('notify-send'):
        try:
            run(['notify-send', 'PasteMD', message])
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            pass


def read_table_source():
    """Clipboard text for table parsing; converts HTML tables via Pandoc when needed."""
    types = [kind.strip() for kind in
             run(['wl-paste', '--list-types']).decode('utf-8', 'replace').splitlines()
             if kind.strip()]
    plain = next((t for t in types if t.lower().startswith('text/plain')), None)
    if plain:
        return run(['wl-paste', '--no-newline', '--type', plain]).decode('utf-8', 'replace')
    html = next((kind for kind in types
                 if kind.partition(';')[0].strip().lower() == 'text/html'), None)
    if html:
        raw = run(['wl-paste', '--no-newline', '--type', html])
        return run(['pandoc', '--from', 'html', '--to', 'gfm'], raw).decode('utf-8', 'replace')
    raise RuntimeError('剪贴板没有文本，无法识别表格。请复制 Markdown 表格或网页表格。')


def table_clipboard_payload(markdown_text):
    """Build the text/html + text/plain payload for WPS 表格, with row count."""
    table = parse_markdown_table(markdown_text)
    if not table:
        raise RuntimeError('剪贴板中没有 Markdown 表格。请复制带 |---| 分隔符的表格，'
                           '或包含表格的网页内容。')
    payload = {'text/html': table_to_html(table).encode('utf-8'),
               'text/plain': table_to_tsv(table).encode('utf-8')}
    return payload, len(table)


TEXT_FORMAT_LABELS = {'md': 'Markdown', 'latex': 'LaTeX', 'html': 'HTML'}


def text_clipboard_label(target_format):
    return TEXT_FORMAT_LABELS.get(target_format, target_format)


def text_clipboard_payload(raw, document, reader, target_format):
    """应用扩展流程：把内容转成 Markdown/LaTeX/HTML 纯文本剪贴板载荷。

    raw 是剪贴板原始字节，document 是 prepare_document 产出的 pandoc JSON AST。
    """
    if target_format == 'md':
        if reader.startswith('markdown'):
            # 原文直通，仅套用公式分隔符修复，避免 Pandoc 重排用户的 Markdown。
            text = convert_latex_delimiters(raw.decode('utf-8', 'replace'))
            return {'text/plain': text.encode('utf-8')}
        text = run(['pandoc', '--from', 'json', '--to', 'gfm'],
                   document).decode('utf-8', 'replace')
        return {'text/plain': text.encode('utf-8')}
    if target_format == 'latex':
        text = run(['pandoc', '--from', 'json', '--to', 'latex'],
                   document).decode('utf-8', 'replace')
        return {'text/plain': text.encode('utf-8')}
    if target_format == 'html':
        if reader.startswith('html'):
            return {'text/html': raw}
        text = run(['pandoc', '--from', 'json', '--to', 'html'],
                   document).decode('utf-8', 'replace')
        return {'text/html': text.encode('utf-8')}
    raise RuntimeError(f'未知的目标格式：{target_format}')


def open_in_wps(path):
    """Open a generated document in host WPS from source, AppImage or Flatpak."""
    if os.environ.get('FLATPAK_ID'):
        command = ['flatpak-spawn', '--host', 'wps', str(path)]
        missing = 'Flatpak 缺少 flatpak-spawn，DOCX 已保存但无法打开 WPS。'
    else:
        command = ['wps', str(path)]
        missing = '找不到 WPS 命令，DOCX 已保存但无法自动打开。'
    if not shutil.which(command[0]):
        raise RuntimeError(missing)
    return subprocess.Popen(command, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)


def math_count(value):
    if isinstance(value, dict):
        return int(value.get('t') == 'Math') + sum(math_count(v) for v in value.values())
    if isinstance(value, list):
        return sum(map(math_count, value))
    return 0


def image_urls(value):
    """Distinct image sources in a Pandoc AST. Pandoc dedupes identical
    media in DOCX, so counting sources (not nodes) matches its output."""
    found = set()
    if isinstance(value, dict):
        if value.get('t') == 'Image' and len(value.get('c', [])) > 2:
            found.add(value['c'][2][0])
        for item in value.values():
            found |= image_urls(item)
    elif isinstance(value, list):
        for item in value:
            found |= image_urls(item)
    return found


def lost_image_count(document, docx):
    """Images the document references but the DOCX media store lacks."""
    expected = image_urls(json.loads(document))
    if not expected:
        return 0
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        embedded = sum(1 for name in archive.namelist()
                       if name.startswith('word/media/'))
    return max(0, len(expected) - embedded)


def native_clipboard_payload(document, plain_text, options=None, reader='markdown'):
    """WPS exposes a DOCX ZIP under this native X11 clipboard format."""
    docx = run(['pandoc', '--from', 'json', '--to', 'docx', '--output', '-']
               + docx_writer_args(options), document)
    docx = finish_docx(docx, reader, options)
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
    expected = math_count(json.loads(document))
    actual = len(root.findall('.//{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath'))
    if actual < expected:
        raise RuntimeError(f'DOCX 仅保留 {actual}/{expected} 个原生公式，剪贴板未修改。')
    return {'Kingsoft WPS 9.0 Format': docx, 'text/plain': plain_text}


def serve_clipboard():
    """Detached XWayland clipboard owner. Data arrives privately over stdin."""
    try:
        from PySide6.QtCore import QMimeData, QTimer
        from PySide6.QtGui import QGuiApplication
        encoded = json.load(sys.stdin)
        payload = {key: base64.b64decode(value, validate=True) for key, value in encoded.items()}
        os.environ['QT_QPA_PLATFORM'] = 'xcb'
        app = QGuiApplication(['PasteMD clipboard'])
        clipboard = app.clipboard()
        mime = QMimeData()
        for kind, data in payload.items():
            mime.setData(kind, data)

        def publish():
            clipboard.setMimeData(mime)
            if not clipboard.ownsClipboard():
                print('ERROR:无法取得 XWayland 剪贴板所有权。', flush=True)
                app.exit(1)
                return
            print('READY', flush=True)

        # Keep ownership until the user copies something else. Never recapture it.
        timer = QTimer()
        timer.timeout.connect(lambda: app.quit() if not clipboard.ownsClipboard() else None)
        timer.start(1000)
        QTimer.singleShot(0, publish)
        return app.exec()
    except Exception as error:
        print('ERROR:' + str(error), flush=True)
        return 1


def clipboard_handshake(process, encoded, timeout=15):
    """Bound both pipe writes and partial replies by one monotonic deadline."""
    writer, reader = process.stdin.fileno(), process.stdout.fileno()
    os.set_blocking(writer, False)
    os.set_blocking(reader, False)
    deadline = time.monotonic() + timeout
    pending = memoryview(encoded)
    reply = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('XWayland 剪贴板服务启动超时。')
        readable, writable, _ = select.select([reader], [writer] if pending else [], [], remaining)
        if writable:
            try:
                pending = pending[os.write(writer, pending[:65536]):]
            except BlockingIOError:
                continue
            if not pending:
                process.stdin.close()
        if readable:
            try:
                chunk = os.read(reader, 4096)
            except BlockingIOError:
                continue
            if not chunk:
                raise RuntimeError('剪贴板服务启动失败：连接已关闭。')
            reply.extend(chunk)
            if len(reply) > 4096:
                raise RuntimeError('剪贴板服务返回了无效响应。')
            if b'\n' in reply:
                line = reply.split(b'\n', 1)[0].decode('utf-8', 'replace').strip()
                if line != 'READY' or pending:
                    raise RuntimeError(line or '剪贴板服务启动失败。')
                return


def set_clipboard_payload(payload):
    if not os.environ.get('DISPLAY'):
        raise RuntimeError('WPS 原生粘贴需要 XWayland（DISPLAY），当前不可用。')
    if importlib.util.find_spec('PySide6') is None:
        raise RuntimeError('缺少 PySide6：sudo dnf install python3-pyside6')
    encoded = json.dumps({key: base64.b64encode(value).decode('ascii')
                          for key, value in payload.items()}).encode()
    command = ([sys.executable, '--serve-clipboard'] if getattr(sys, 'frozen', False)
               else [sys.executable, str(Path(__file__).resolve()), '--serve-clipboard'])
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True, env=dict(os.environ, QT_QPA_PLATFORM='xcb'),
    )
    try:
        clipboard_handshake(process, encoded)
    except Exception:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
    return process.pid


def main(argv=None, options=None):
    if argv is None and sys.argv[1:] == ['--serve-clipboard']:
        return serve_clipboard()
    parser = argparse.ArgumentParser(description='Fedora Wayland 剪贴板转换（实验版）')
    parser.add_argument('--demo', action='store_true', help='转换内置公式样本，用于验证 WPS 粘贴，无需先复制来源')
    parser.add_argument('--input', choices=['auto', 'markdown', 'html'], default='auto')
    output = parser.add_mutually_exclusive_group()
    output.add_argument('--clipboard', action='store_true', help='写入 WPS 原生 DOCX 剪贴板（默认），随后在 WPS 按 Ctrl+V')
    output.add_argument('--table', action='store_true', help='识别剪贴板中的 Markdown 表格，写入 HTML 表格剪贴板（适配 WPS 表格）')
    output.add_argument('--as', dest='as_format', choices=['md', 'latex', 'html'],
                        help='应用扩展流程：按 Markdown/LaTeX/HTML 纯文本写入剪贴板')
    output.add_argument('--docx', action='store_true', help='保存 DOCX，而不是替换剪贴板')
    output.add_argument('--open', action='store_true', help='用 WPS 打开生成的 DOCX（隐含 --docx）')
    args = parser.parse_args(argv)
    open_docx = args.open
    use_clipboard = not (args.docx or args.open or args.table or args.as_format)
    options = load_conversion_options() if options is None else options
    try:
        if not os.environ.get('WAYLAND_DISPLAY'):
            raise RuntimeError('请在 Wayland 桌面会话中运行。')
        required = ['pandoc'] + ([] if args.demo else ['wl-paste'])
        if open_docx:
            required.append('flatpak-spawn' if os.environ.get('FLATPAK_ID') else 'wps')
        missing = [tool for tool in required if not shutil.which(tool)]
        if missing:
            raise RuntimeError('缺少命令：' + ', '.join(missing) + '。转换依赖：sudo dnf install pandoc wl-clipboard python3-pyside6；WPS 需单独安装。')
        if args.table:
            payload, rows = table_clipboard_payload(read_table_source())
            set_clipboard_payload(payload)
            message = f'表格已就绪（{rows} 行），请在 WPS 表格中按 Ctrl+V。'
            notify(message)
            print(message)
            return 0
        if args.as_format:
            if args.demo:
                content = DEMO_MARKDOWN.encode('utf-8')
                reader = 'markdown' + MATH_EXTENSIONS
            else:
                content, reader = read_clipboard(args.input)
            if not content.strip():
                raise RuntimeError('剪贴板内容为空。')
            raw_source = content
            content = prepare_document(content, reader, options)
            payload = text_clipboard_payload(raw_source, content, reader, args.as_format)
            set_clipboard_payload(payload)
            message = f'已按{text_clipboard_label(args.as_format)}文本写入剪贴板，在目标应用中按 Ctrl+V。'
            notify(message)
            print(message)
            return 0
        if args.demo:
            content = DEMO_MARKDOWN.encode('utf-8')
            reader = 'markdown' + MATH_EXTENSIONS
        else:
            content, reader = read_clipboard(args.input)
        if not content.strip():
            raise RuntimeError('剪贴板内容为空。')
        content = prepare_document(content, reader, options)
        command = ['pandoc', '--from', 'json']
        if not use_clipboard:
            cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'pastemd'
            cache.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(suffix='.docx', prefix='paste-', dir=cache)
            os.close(fd)
            try:
                docx = run(command + docx_writer_args(options)
                           + ['--to', 'docx', '--output', '-'], content)
                docx = finish_docx(docx, reader, options)
                Path(name).write_bytes(docx)
            except Exception:
                Path(name).unlink(missing_ok=True)
                raise
            print(name)
            if open_docx:
                open_in_wps(name)
            message = 'DOCX 已保存：' + name
            lost = lost_image_count(content, docx)
            if lost:
                message += f'（注意：{lost} 张图片未能嵌入）'
            notify(message)
            print(message)
        else:
            plain_text = run(command + ['--to', 'plain'], content)
            payload = native_clipboard_payload(content, plain_text, options, reader)
            lost = lost_image_count(content, payload['Kingsoft WPS 9.0 Format'])
            set_clipboard_payload(payload)
            message = '公式富文本已就绪，请在 WPS 的 .docx 文档中按 Ctrl+V。'
            if lost:
                message += f'注意：{lost} 张图片未能嵌入。'
            notify(message)
            print(message)
        return 0
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, ET.ParseError, subprocess.TimeoutExpired) as error:
        message = str(error)
        print('PasteMD: ' + message, file=sys.stderr)
        notify(message)
        return 1


if __name__ == '__main__':
    sys.exit(main())
